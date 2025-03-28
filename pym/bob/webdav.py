from . import BOB_VERSION
from .errors import BuildError
from .utils import sslNoVerifyContext
import base64
import http.client
import os
from urllib.parse import unquote, urlsplit
from xml.etree.ElementTree import fromstring

class HTTPException(Exception):
    pass

class HttpDownloadError(HTTPException):
    def __init__(self, reason):
        self.reason = reason

class HttpUploadError(HTTPException):
    def __init__(self, reason):
        self.reason = reason

class HttpNotFoundError(HTTPException):
    pass

class HttpAlreadyExistsError(HTTPException):
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
        self.__sslVerify = sslVerify

    def getPartialDownloader(self, path, length=512*1024):
        return self.PartialDownloader(self, path, length)

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

    def exists(self, path):
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

    def download(self, path, offset=None, length=None):
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
        connection = self._getConnection()
        connection.request("PUT", path, buf, headers=headers)
        response = connection.getresponse()
        response.read()
        if response.status == 412:
            # precondition failed -> lost race with other upload
            raise HttpAlreadyExistsError()
        elif response.status not in [200, 201, 204]:
            raise HttpUploadError("PUT {} {}".format(response.status, response.reason))

    def _mkdir(self, path):
        # MKCOL resources must have a trailing slash because they are
        # directories. Otherwise Apache might send a HTTP 301. Nginx refuses to
        # create the directory with a 409 which looks odd.
        if not path.endswith("/"):
            path += "/"
        connection = self._getConnection()
        connection.request("MKCOL", path, headers=self._getHeaders())
        response = connection.getresponse()
        response.read()
        return response

    def mkdir(self, path, depth=1):
        if depth > 0:
            response = self._mkdir(path)
            if response.status == 409:
                (_path, _, _) = path.rpartition("/")
                self.mkdir(_path, depth - 1)
                response = self._mkdir(path)
            # We expect to create the directory (201) or it already existed (405).
            # If the server does not support MKCOL we'd expect a 405 too and hope
            # for the best...
            if response.status not in [201, 405]:
                raise HttpUploadError("MKCOL {} {}".format(response.status, response.reason))

    def listdir(self, path):
        base_path = self.__url.path
        # create a full path ending with trailing / (should prevent http 301 - moved permanently)
        path = '/'.join([base_path, path.strip('/'), ''])
        if self.exists(path):
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

    def delete(self, filename):
        base_path = self.__url.path
        # create a full path
        filepath = '/'.join([base_path, filename.strip('/')])
        headers = self._getHeaders()
        connection = self._getConnection()
        connection.request("DELETE", filepath, headers=headers)
        response = connection.getresponse()
        response.read()
        if response.status not in [200, 204, 404]:
            raise HttpDownloadError("DELETE {} {}".format(response.status, response.reason))

    def stat(self, file):
        base_path = self.__url.path
        # create a full path
        filepath = '/'.join([base_path, file.strip('/')])
        if self.exists(filepath):
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
