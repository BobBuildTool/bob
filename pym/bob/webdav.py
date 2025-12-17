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

class WebdavDownloadError(WebdavError):
    def __init__(self, reason):
        self.reason = reason

class WebdavUploadError(WebdavError):
    def __init__(self, reason):
        self.reason = reason

class WebdavNotFoundError(WebdavError):
    pass

class WebdavAlreadyExistsError(WebdavError):
    pass

class WebDav:

    class PartialDownloader:
        def __init__(self, webdav, path, length=512*1024):
            self.__webdav = webdav
            self.__path = path
            self.__data = bytearray(self.__webdav.download(self.__path, 0, length).read())
            self.__offset = length

        def get(self):
            return self.__data

        def more(self, length=512*1024):
            new_data = self.__webdav.download(self.__path, self.__offset, length)
            self.__offset += length
            self.__data.extend(new_data.read())
            return self.__data

    def __init__(self, url, sslVerify=True):
        self.__url = url
        self.__connection = None
        self.__context = None if sslVerify else sslNoVerifyContext()

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

    def _getURL(self, path):
        # remove username and password from URI
        netloc = self.__url.netloc
        if self.__url.username is not None:
            netloc = self.__url.netloc.split('@')[1]

        return urlunsplit((self.__url.scheme, netloc, path,
                           self.__url.query, self.__url.fragment))

    def exists(self, path):
        req = urllib.request.Request (self._getURL(path),
                                      headers=self._getHeaders(), method="HEAD")
        try:
            with urllib.request.urlopen (req, context=self.__context):
                pass
            return True
        except urllib.error.HTTPError as e:
            e.fp.read()
            if e.status != 404:
                raise WebdavUploadError("HEAD {} {}".format(e.status, e.reason))
        except (http.client.HTTPException, OSError) as e:
            raise WebdavUploadError(str(e))

        return False

    def download(self, path, offset=None, length=None):
        headers = self._getHeaders()
        if offset is not None and length is not None:
            headers.update({'Range': 'bytes={}-{}'.format(offset, offset + length - 1)})

        req = urllib.request.Request (self._getURL(path),
                                      headers=headers, method="GET")
        try:
            return urllib.request.urlopen (req, context=self.__context)
        except urllib.error.HTTPError as e:
            e.fp.read()
            if e.status == 404:
                raise WebdavNotFoundError()
            else:
                raise WebdavDownloadError("{} {}".format(e.status, e.reason))
        except (http.client.HTTPException, OSError) as e:
            raise WebdavDownloadError(str(e))

    def upload(self, path, buf, overwrite):
        # Determine file length ourselves and add a "Content-Length" header. This
        # used to work in Python 3.5 automatically but was removed later.
        buf.seek(0, os.SEEK_END)
        length = str(buf.tell())
        buf.seek(0)
        headers = self._getHeaders()
        headers.update({'Content-Length': length})
        if not overwrite:
            headers.update({'If-None-Match': '*'})

        req = urllib.request.Request (self._getURL(path),
                                      data=buf, headers=headers, method="PUT")
        try:
            with urllib.request.urlopen (req, context=self.__context) as resp:
                if resp.status not in [200, 201, 204]:
                    raise WebdavUploadError("PUT {} {}".format(resp.status, resp.reason))
        except urllib.error.HTTPError as e:
            e.fp.read()
            if e.status == 412:
                # precondition failed -> lost race with other upload
                raise WebdavAlreadyExistsError()
            raise WebdavUploadError("PUT {} {}".format(e.status, e.reason))
        except (http.client.HTTPException, OSError) as e:
            raise WebdavUploadError(str(e))

    def _mkdir(self, path):
        # MKCOL resources must have a trailing slash because they are
        # directories. Otherwise Apache might send a HTTP 301. Nginx refuses to
        # create the directory with a 409 which looks odd.
        if not path.endswith("/"):
            path += "/"

        req = urllib.request.Request (self._getURL(path),
                                      headers=self._getHeaders(), method="MKCOL")
        try:
            with urllib.request.urlopen (req, context=self.__context) as resp:
                return (resp.status, None)
        except urllib.error.HTTPError as e:
            e.fp.read()
            return (e.status, e.reason)
        except (http.client.HTTPException, OSError) as e:
            raise WebdavUploadError(str(e))

    def mkdir(self, path, depth=1):
        if depth > 0:
            status, reason = self._mkdir(path)
            if status == 409:
                (_path, _, _) = path.rpartition("/")
                self.mkdir(_path, depth - 1)
                status,reason = self._mkdir(path)
            # We expect to create the directory (201) or it already existed (405).
            # If the server does not support MKCOL we'd expect a 405 too and hope
            # for the best...
            if status not in [201, 405]:
                raise WebdavUploadError("MKCOL {} {}".format(status, reason))

    def listdir(self, path):
        base_path = self.__url.path
        # create a full path ending with trailing / (should prevent http 301 - moved permanently)
        path = '/'.join([base_path, path.strip('/'), ''])
        dir_infos = []
        if self.exists(path):
            headers = self._getHeaders()
            # Depth: 1 - applies to the resource and the immediate children (infinity usually prohibited by server)
            headers.update({'Depth': '1'})
            req = urllib.request.Request (self._getURL(path),
                                          headers=headers, method="PROPFIND")
            content = None
            try:
                with urllib.request.urlopen (req, context=self.__context) as response:
                    if response.status not in [207]:
                        raise WebdavDownloadError("PROPFIND {} {}".format(response.status, response.reason))
                    content = response.read()
            except urllib.error.HTTPError as e:
                e.fp.read()
                raise WebdavDownloadError("PROPFIND {} {}".format(e.status, e.reason))
            except (http.client.HTTPException, OSError) as e:
                raise WebdavDownloadError(str(e))
            # get all dav responses from multistatusresponse
            tree = fromstring(content)
            for resp in tree.findall(".//{DAV:}response"):
                # only need the path in case the full URL is included
                href = unquote(urlsplit(resp.findtext(".//{DAV:}href")).path)
                # exclude base path
                if href.strip('/') == path.strip('/'):
                    continue
                dir_info = dict()
                # collect if it is a dir, the href and self defined path (href without base path)
                dir_info['is_dir'] = resp.find(".//{DAV:}collection") is not None
                dir_info['href'] = href
                dir_info['path'] = href[len(base_path):].strip('/')
                dir_infos.append(dir_info)
        return dir_infos

    def delete(self, filename):
        base_path = self.__url.path
        # create a full path
        filepath = '/'.join([base_path, filename.strip('/')])
        headers = self._getHeaders()
        req = urllib.request.Request (self._getURL(filepath),
                                      headers=headers, method="DELETE")
        status = reason = None
        try:
            with urllib.request.urlopen (req, context=self.__context) as response:
                status = response.status
        except urllib.error.HTTPError as e:
            e.fp.read()
            status = e.status
            reason = e.reason
        except (http.client.HTTPException, OSError) as e:
            raise WebdavDownloadError(str(e))
        if status not in [200, 204, 404]:
            raise WebdavDownloadError("DELETE {} {}".format(status, reason))

    def stat(self, file):
        base_path = self.__url.path
        # create a full path
        filepath = '/'.join([base_path, file.strip('/')])
        if self.exists(filepath):
            headers = self._getHeaders()
            # Depth: 0 - applies to the resource itself
            headers.update({'Depth': '0'})

            req = urllib.request.Request (self._getURL(filepath),
                                          headers=headers, method="PROPFIND")
            content = None
            try:
                with urllib.request.urlopen (req) as response:
                    if response.status not in [207]:
                        raise WebdavDownloadError("PROPFIND {} {}".format(response.status, response.reason))
                    # get response
                    content = response.read()
            except urllib.error.HTTPError as e:
                e.fp.read()
                raise WebdavDownloadError("PROPFIND {} {}".format(e.status, e.reason))
            except (http.client.HTTPException, OSError) as e:
                raise WebdavDownloadError(str(e))

            # parse tree from content
            tree = fromstring(content)
            # get response tag tree
            resp = tree.find(".//{DAV:}response")
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
            return stats
