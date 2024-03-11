from . import BOB_VERSION
from .errors import BuildError
from .utils import sslNoVerifyContext
import base64
import http.client
import os
from urllib.parse import unquote, urlsplit, urlparse
from xml.etree.ElementTree import fromstring

class HttpDownloadError(http.client.HTTPException):
    def __init__(self, reason):
        self.reason = reason

class HttpUploadError(http.client.HTTPException):
    def __init__(self, reason):
        self.reason = reason

class HttpNotFoundError(http.client.HTTPException):
    pass

class HttpAlreadyExistsError(http.client.HTTPException):
    pass

class WebDav:

    class PartialDownloader:
        def __init__(self, webdav, path, length=512*1024):
            self.__webdev = webdav
            self.__path = path
            self.__data = bytearray(self.__webdev.download(self.__path, 0, length).read())
            self.__offset = length

        def get(self):
            return self.__data

        def more(self, length=512*1024):
            new_data = self.__webdev.download(self.__path, self.__offset, length)
            self.__offset += length
            self.__data.extend(new_data.read())
            return self.__data

    def __init__(self, spec):
        self.__url = urlparse(spec["url"])
        self.__connection = None
        self.__sslVerify = spec.get("sslVerify", True)

    def __retry(self, request):
        retry = True
        while True:
            try:
                return (True, request())
            except (http.client.HTTPException, OSError) as e:
                self._resetConnection()
                if not retry: return (False, e)
                retry = False

    def getPartialDownloader(self, path):
        return self.PartialDownloader(self, path)

    def _getConnection(self):
        if self.__connection is not None:
            return self.__connection

        url = self.__url
        if url.scheme == 'http':
            connection = http.client.HTTPConnection(url.hostname, url.port)
        elif url.scheme == 'https':
            ctx = None if self.__sslVerify else sslNoVerifyContext()
            connection = http.client.HTTPSConnection(url.hostname, url.port,
                                                     context=ctx)
        else:
            raise BuildError("Unsupported URL scheme: '{}'".format(url.scheme))

        # simple check if webdav is supported
        connection.request("OPTIONS", '/'.join([self.__url.path, '']), headers=self._getHeaders())

        response = connection.getresponse()
        response.read()
        dav_supported = response.getheader("DAV") is not None
        if not dav_supported:
            raise BuildError("WEBDAV not supported by server")

        self.__connection = connection
        return connection

    def _resetConnection(self):
        if self.__connection is not None:
            self.__connection.close()
            self.__connection = None

    def _getHeaders(self):
        headers = {'User-Agent': 'BobBuildTool/{}'.format(BOB_VERSION)}
        if self.__url.username is not None:
            username = unquote(self.__url.username)
            passwd = unquote(self.__url.password)
            userPass = username + ":" + passwd
            headers['Authorization'] = 'Basic ' + base64.b64encode(
                userPass.encode("utf-8")).decode("ascii")
        return headers

    def _check(self, path):
        connection = self._getConnection()
        connection.request("HEAD", path, headers=self._getHeaders())
        response = connection.getresponse()
        response.read()
        if response.status == 200:
            return True
        elif response.status == 404:
            return False
        else:
            raise HttpUploadError("HEAD {} {}".format(response.status, response.reason))

    def check(self, path):
        (ok, result) = self.__retry(lambda: self._check(path))
        if ok:
            return result
        else:
            raise result

    def _download(self, path, offset=None, length=None):
        connection = self._getConnection()
        headers = self._getHeaders()
        if offset is not None and length is not None:
            headers.update({'Range': 'bytes={}-{}'.format(offset, offset + length - 1)})
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        if response.status in [200, 206]:
            return response
        else:
            response.read()
            if response.status == 404:
                raise HttpNotFoundError()
            else:
                raise HttpDownloadError("{} {}".format(response.status,
                                                           response.reason))

    def download(self, path, offset=None, length=None):
        (ok, result) = self.__retry(lambda: self._download(path, offset, length))
        if ok:
            return result
        else:
            raise result

    def _upload(self, path, buf, overwrite):
        # Determine file length ourselves and add a "Content-Length" header. This
        # used to work in Python 3.5 automatically but was removed later.
        buf.seek(0, os.SEEK_END)
        length = str(buf.tell())
        buf.seek(0)
        headers = self._getHeaders()
        headers.update({'Content-Length': length})
        if not overwrite:
            headers.update({'If-None-Match': '*'})
        connection = self._getConnection()
        connection.request("PUT", path, buf, headers=headers)
        response = connection.getresponse()
        response.read()
        if response.status == 412:
            # precondition failed -> lost race with other upload
            raise HttpAlreadyExistsError()
        elif response.status not in [200, 201, 204]:
            raise HttpUploadError("PUT {} {}".format(response.status, response.reason))

    def upload(self, path, buf, overwrite):
        (ok, result) = self.__retry(lambda: self._upload(path, buf, overwrite))
        if ok:
            return result
        else:
            raise result

    def _mkdir(self, path):
        if not self.check(path):
            headers = self._getHeaders()
            connection = self._getConnection()
            connection.request("MKCOL", path, headers=headers)
            response = connection.getresponse()
            response.read()
            if response.status not in [200, 201]:
                raise HttpUploadError("MKCOL {} {}".format(response.status, response.reason))

    def mkdir(self, path):
        (ok, result) = self.__retry(lambda: self._mkdir(path))
        if ok:
            return result
        else:
            raise result

    def _list(self, path):
        base_path = self.__url.path
        # create a full path ending with trailing / (should prevent http 301 - moved permanently)
        path = '/'.join([base_path, path.strip('/'), ''])
        if self.check(path):
            headers = self._getHeaders()
            # Depth: 1 - applies to the resource and the immediate children (infinity usually prohibited by server)
            headers.update({'Depth': '1'})
            connection = self._getConnection()
            connection.request("PROPFIND", path, headers=headers)
            response = connection.getresponse()
            if response.status not in [207]:
                raise HttpDownloadError("PROPFIND {} {}".format(response.status, response.reason))
            content = response.read()
            # get all dav responses from multistatusresponse
            tree = fromstring(content)
            dir_infos = []
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

    def list(self, path):
        (ok, result) = self.__retry(lambda: self._list(path))
        if ok:
            return result
        else:
            raise result

    def _delete(self, filename):
        base_path = self.__url.path
        # create a full path
        filepath = '/'.join([base_path, filename.strip('/')])
        headers = self._getHeaders()
        connection = self._getConnection()
        connection.request("DELETE", filepath, headers=headers)
        response = connection.getresponse()
        response.read()
        if response.status not in [200, 204]:
            raise HttpDownloadError("DELETE {} {}".format(response.status, response.reason))

    def delete(self, filename):
        (ok, result) = self.__retry(lambda: self._delete(filename))
        if ok:
            return result
        else:
            raise result

    def _stat(self, file):
        base_path = self.__url.path
        # create a full path
        filepath = '/'.join([base_path, file.strip('/')])
        if self.check(filepath):
            headers = self._getHeaders()
            # Depth: 0 - applies to the resource itself
            headers.update({'Depth': '0'})
            connection = self._getConnection()
            connection.request("PROPFIND", filepath, headers=headers)
            response = connection.getresponse()
            if response.status not in [207]:
                raise HttpDownloadError("PROPFIND {} {}".format(response.status, response.reason))
            # get response
            content = response.read()
            # parse tree from content
            tree = fromstring(content)
            # get response tag tree
            resp = tree.find(".//{DAV:}response")
            stats = dict()
            stats['cdate'] = resp.find(".//{DAV:}creationdate").text
            stats['mdate'] = resp.find(".//{DAV:}getlastmodified").text
            stats['len'] = int(resp.find(".//{DAV:}getcontentlength").text)
            stats['etag'] = resp.find(".//{DAV:}getetag").text
            return stats

    def stat(self, path):
        (ok, result) = self.__retry(lambda: self._stat(path))
        if ok:
            return result
        else:
            raise result
