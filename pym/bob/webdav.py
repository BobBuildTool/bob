from . import BOB_VERSION
from .errors import BuildError
from .utils import sslNoVerifyContext
import base64
import sys
import os
import urllib.request
from urllib.parse import unquote, urlsplit, urlunsplit
from xml.etree.ElementTree import fromstring
import http.client

class WebdavError(Exception):
    pass

class WebdavNotFoundError(WebdavError):
    pass

class WebdavAlreadyExistsError(WebdavError):
    pass


def getNetLoc(url):
    """Get the network location of a parsed URL without the credentials.

    The user name and password of the HTTP basic authentication are part of
    the URL. They are sent in the Authorization header instead and must be
    kept out of the request URL and of anything that is shown to the user.
    """
    netloc = url.netloc
    if url.username is not None:
        # urlparse() delimits the user info at the *last* '@'. Cut at the same
        # one, otherwise a password with an unencoded '@' yields a bogus host.
        netloc = netloc.rsplit('@', 1)[1]

    return netloc


class WebDav:

    class PartialDownloader:
        def __init__(self, webdav, path, length=512*1024):
            self.__webdav = webdav
            self.__path = path
            self.__data = bytearray(self.__webdav.download(self.__path, 0, length))
            self.__offset = len(self.__data)

        def get(self):
            return self.__data

        def more(self, length=512*1024):
            new_data = self.__webdav.download(self.__path, self.__offset, length)
            self.__offset += len(new_data)
            self.__data.extend(new_data)
            return self.__data

    def __init__(self, url, sslVerify=True, retries=0):
        self.__url = url
        self.__connection = None
        self.__sslVerify = sslVerify
        self.__retries = retries

    def __retry(self, request):
        """Run a request, retrying transient transport errors."""
        retries = self.__retries
        while True:
            try:
                return request()
            except (WebdavError, OSError) as e:
                if retries <= 0: raise
                retries -= 1

    def __createContext(self):
        # Create the SSL context on demand. Holding it as an attribute would
        # render the object unpicklable, but the archive backends are sent to
        # the up-/download executor processes.
        return None if self.__sslVerify else sslNoVerifyContext()

    def getPartialDownloader(self, path, length=512*1024):
        return self.PartialDownloader(self, path, length)

    def _getHeaders(self):
        headers = {'User-Agent': 'BobBuildTool/{}'.format(BOB_VERSION)}
        if self.__url.username is not None:
            username = unquote(self.__url.username)
            passwd = unquote(self.__url.password)
            userPass = username + ":" + passwd
            headers['Authorization'] = 'Basic ' + base64.b64encode(
                userPass.encode("utf-8")).decode("ascii")
        return headers

    def __getRequestPath(self, path):
        path = path.lstrip("/")
        if path:
            return "/".join([self.__url.path.rstrip("/"), path])
        else:
            return self.__url.path

    def getRequestURL(self, path, query=None):
        if query is None:
            query = self.__url.query
        return urlunsplit((self.__url.scheme, getNetLoc(self.__url),
                           self.__getRequestPath(path), query, ""))

    def exists(self, path):
        return self.__retry(lambda: self.__exists(path))

    def __exists(self, path):
        req = urllib.request.Request(self.getRequestURL(path),
                                     headers=self._getHeaders(), method="HEAD")
        try:
            with urllib.request.urlopen (req, context=self.__createContext()):
                pass
            return True
        except urllib.error.HTTPError as e:
            e.fp.read()
            if e.status != 404:
                raise WebdavError("HEAD {} {}".format(e.status, e.reason))
        except (http.client.HTTPException, OSError) as e:
            raise WebdavError(str(e))

        return False

    def openDownload(self, path, offset=None, length=None, query=None):
        return self.__retry(lambda: self.__openDownload(path, offset, length, query))

    def __openDownload(self, path, offset, length, query):
        headers = self._getHeaders()
        if offset is not None and length is not None:
            headers.update({'Range': 'bytes={}-{}'.format(offset, offset + length - 1)})

        req = urllib.request.Request(self.getRequestURL(path, query),
                                     headers=headers, method="GET")
        try:
            return urllib.request.urlopen (req, context=self.__createContext())
        except urllib.error.HTTPError as e:
            e.fp.read()
            if e.status == 404:
                raise WebdavNotFoundError()
            else:
                raise WebdavError("{} {}".format(e.status, e.reason))
        except (http.client.HTTPException, OSError) as e:
            raise WebdavError(str(e))

    def download(self, path, offset=None, length=None, query=None):
        return self.__retry(lambda: self.__download(path, offset, length, query))

    def __download(self, path, offset, length, query):
        with self.__openDownload(path, offset, length, query) as resp:
            return resp.read()

    def upload(self, path, buf, overwrite):
        return self.__retry(lambda: self.__upload(path, buf, overwrite))

    def __upload(self, path, buf, overwrite):
        # Determine file length ourselves and add a "Content-Length" header. This
        # used to work in Python 3.5 automatically but was removed later.
        buf.seek(0, os.SEEK_END)
        length = str(buf.tell())
        buf.seek(0)
        headers = self._getHeaders()
        headers.update({'Content-Length': length, 'Content-Type': 'application/octet-stream'})
        if not overwrite:
            headers.update({'If-None-Match': '*'})

        req = urllib.request.Request(self.getRequestURL(path),
                                     data=buf, headers=headers, method="PUT")
        try:
            with urllib.request.urlopen (req, context=self.__createContext()) as resp:
                if resp.status not in [200, 201, 204]:
                    raise WebdavError("PUT {} {}".format(resp.status, resp.reason))
        except urllib.error.HTTPError as e:
            e.fp.read()
            if e.status == 412:
                # precondition failed -> lost race with other upload
                raise WebdavAlreadyExistsError()
            if e.status == 409:
                # Some servers (e.g. the Gitea package registry) do not honour
                # "If-None-Match" but refuse to overwrite an existing file with
                # a conflict instead.
                raise WebdavAlreadyExistsError()
            raise WebdavError("PUT {} {}".format(e.status, e.reason))
        except (http.client.HTTPException, OSError) as e:
            raise WebdavError(str(e))

    def mkdir(self, path):
        status, reason = self.__retry(lambda: self.__mkdir(path))
        if status == 409:
            (parent_path, _, _) = path.rpartition("/")
            if parent_path:
                self.mkdir(parent_path)
                status, reason = self.__retry(lambda: self.__mkdir(path))
        # We expect to create the directory (201) or it already existed (405).
        # If the server does not support MKCOL we'd expect a 405 too and hope
        # for the best...
        if status not in [201, 405]:
            raise WebdavError("MKCOL {} {}".format(status, reason))

    def __mkdir(self, path):
        # MKCOL resources must have a trailing slash because they are
        # directories. Otherwise Apache might send a HTTP 301. Nginx refuses to
        # create the directory with a 409 which looks odd.
        if not path.endswith("/"):
            path += "/"

        req = urllib.request.Request(self.getRequestURL(path),
                                     headers=self._getHeaders(), method="MKCOL")
        try:
            with urllib.request.urlopen (req, context=self.__createContext()) as resp:
                status, reason = resp.status, None
        except urllib.error.HTTPError as e:
            e.fp.read()
            status, reason = e.status, e.reason
        except (http.client.HTTPException, OSError) as e:
            raise WebdavError(str(e))

        # Only the status codes that mkdir() interprets itself are passed
        # through. Everything else (e.g. a transient 500) is a plain error
        # that must be retried instead of being handled as a valid result.
        if status not in [201, 405, 409]:
            raise WebdavError("MKCOL {} {}".format(status, reason))
        return (status, reason)

    def listdir(self, path):
        return self.__retry(lambda: self.__listdir(path))

    def __listdir(self, path):
        # create a full path ending with trailing / (should prevent http 301 - moved permanently)
        if not path.endswith("/"):
            path += "/"
        dir_infos = []
        if self.exists(path):
            headers = self._getHeaders()
            # Depth: 1 - applies to the resource and the immediate children (infinity usually prohibited by server)
            headers.update({'Depth': '1'})
            req = urllib.request.Request(self.getRequestURL(path),
                                         headers=headers, method="PROPFIND")
            content = None
            try:
                with urllib.request.urlopen (req, context=self.__createContext()) as response:
                    if response.status not in [207]:
                        raise WebdavError("PROPFIND {} {}".format(response.status, response.reason))
                    content = response.read()
            except urllib.error.HTTPError as e:
                e.fp.read()
                raise WebdavError("PROPFIND {} {}".format(e.status, e.reason))
            except (http.client.HTTPException, OSError) as e:
                raise WebdavError(str(e))
            # get all dav responses from multistatusresponse
            tree = fromstring(content)
            base_path = self.__url.path
            request_path = self.__getRequestPath(path)
            for resp in tree.findall(".//{DAV:}response"):
                # only need the path in case the full URL is included
                href = unquote(urlsplit(resp.findtext(".//{DAV:}href")).path)
                # exclude base path
                if href.strip('/') == request_path.strip('/'):
                    continue
                # collect stat infos
                stats = dict()
                stats['mdate'] = resp.find(".//{DAV:}getlastmodified")
                if stats['mdate'] is not None:
                    stats['mdate'] = stats['mdate'].text
                stats['len'] = resp.find(".//{DAV:}getcontentlength")
                if stats['len'] is not None:
                    stats['len'] = int(stats['len'].text)
                stats['etag'] = resp.find(".//{DAV:}getetag")
                if stats['etag'] is not None:
                    stats['etag'] = stats['etag'].text
                # collect if it is a dir, the href and self defined path (href without base path)
                stats['is_dir'] = resp.find(".//{DAV:}collection") is not None
                stats['href'] = href
                stats['path'] = href[len(base_path):].strip('/')
                dir_infos.append(stats)
        return dir_infos

    def delete(self, path):
        return self.__retry(lambda: self.__delete(path))

    def __delete(self, path):
        headers = self._getHeaders()
        req = urllib.request.Request(self.getRequestURL(path),
                                     headers=headers, method="DELETE")
        status = reason = None
        try:
            with urllib.request.urlopen (req, context=self.__createContext()) as response:
                status = response.status
        except urllib.error.HTTPError as e:
            e.fp.read()
            status = e.status
            reason = e.reason
        except (http.client.HTTPException, OSError) as e:
            raise WebdavError(str(e))
        if status not in [200, 204, 404]:
            raise WebdavError("DELETE {} {}".format(status, reason))
