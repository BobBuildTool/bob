# Bob build tool
# Copyright (C) 2016  Jan Klötzke
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from ..errors import BuildError
from ..tty import colorize
from ..utils import asHexStr, removePath
from tempfile import TemporaryFile
from pipes import quote
import os.path
import tarfile
import textwrap
import urllib.request, urllib.error

class DummyArchive:
    """Archive that does nothing"""

    def wantDownload(self, enable):
        pass

    def wantUpload(self, enable):
        pass

    def canDownload(self):
        return False

    def canUpload(self):
        return False

    def uploadPackage(self, buildId, path):
        pass

    def downloadPackage(self, buildId, path):
        return False

    def upload(self, step, buildIdFile, tgzFile):
        return ""

    def download(self, step, buildIdFile, tgzFile):
        return ""

class LocalArchive:
    def __init__(self, spec):
        self.__basePath = os.path.abspath(spec["path"])
        self.__download = False
        self.__upload = False

    def wantDownload(self, enable):
        self.__download = enable

    def wantUpload(self, enable):
        self.__upload = enable

    def canDownload(self):
        return self.__download

    def canUpload(self):
        return self.__upload

    def uploadPackage(self, buildId, path):
        if not self.__upload:
            return

        packageResultId = asHexStr(buildId)
        packageResultPath = os.path.join(self.__basePath, packageResultId[0:2],
                                         packageResultId[2:4])
        packageResultFile = os.path.join(packageResultPath,
                                         packageResultId[4:]) + ".tgz"
        if os.path.isfile(packageResultFile):
            print("   UPLOAD    skipped ({} exists in archive)".format(path))
            return

        print(colorize("   UPLOAD    {}".format(path), "32"))
        if not os.path.isdir(packageResultPath): os.makedirs(packageResultPath)
        with tarfile.open(packageResultFile, "w:gz") as tar:
            tar.add(path, arcname=".")

    def downloadPackage(self, buildId, path):
        if not self.__download:
            return False

        print(colorize("   DOWNLOAD  {}...".format(path), "32"), end="")
        packageResultId = asHexStr(buildId)
        packageResultPath = os.path.join(self.__basePath, packageResultId[0:2],
                                         packageResultId[2:4])
        packageResultFile = os.path.join(packageResultPath,
                                         packageResultId[4:]) + ".tgz"
        if os.path.isfile(packageResultFile):
            removePath(path)
            os.makedirs(path)
            with tarfile.open(packageResultFile, "r:gz") as tar:
                tar.extractall(path)
            print(colorize("ok", "32"))
            return True
        else:
            print(colorize("not found", "33"))
            return False

    def upload(self, step, buildIdFile, tgzFile):
        return ""

    def download(self, step, buildIdFile, tgzFile):
        return ""


class SimpleHttpArchive:
    def __init__(self, spec):
        self.__url = spec["url"]
        self.__download = False
        self.__upload = False

    def _makeUrl(self, buildId):
        packageResultId = asHexStr(buildId)
        return "/".join([self.__url, packageResultId[0:2], packageResultId[2:4],
            packageResultId[4:] + ".tgz"])

    def wantDownload(self, enable):
        self.__download = enable

    def wantUpload(self, enable):
        self.__upload = enable

    def canDownload(self):
        return self.__download

    def canUpload(self):
        return self.__upload

    def uploadPackage(self, buildId, path):
        if not self.__upload:
            return

        # check if already there
        url = self._makeUrl(buildId)
        try:
            try:
                req = urllib.request.Request(url=url, method='HEAD')
                f = urllib.request.urlopen(req)
                print("   UPLOAD    skipped ({} exists in archive)".format(path))
                return
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    raise BuildError("Error for HEAD on "+url+": "+e.reason)

            print(colorize("   UPLOAD    {}".format(path), "32"))
            with TemporaryFile() as tmpFile:
                with tarfile.open(fileobj=tmpFile, mode="w:gz") as tar:
                    tar.add(path, arcname=".")
                tmpFile.seek(0)
                req = urllib.request.Request(url=url, data=tmpFile.read(),
                                             method='PUT')
                f = urllib.request.urlopen(req)
        except urllib.error.URLError as e:
            raise BuildError("Error uploading package: "+str(e.reason))

    def downloadPackage(self, buildId, path):
        if not self.__download:
            return False

        ret = False
        print(colorize("   DOWNLOAD  {}...".format(path), "32"), end="")
        url = self._makeUrl(buildId)
        try:
            (localFilename, headers) = urllib.request.urlretrieve(url)
            removePath(path)
            os.makedirs(path)
            with tarfile.open(localFilename, "r:gz", errorlevel=1) as tar:
                tar.extractall(path)
            ret = True
            print(colorize("ok", "32"))
        except urllib.error.URLError as e:
            print(colorize(str(e.reason), "33"))
        except OSError as e:
            raise BuildError("Error: " + str(e))
        finally:
            urllib.request.urlcleanup()

        return ret

    def upload(self, step, buildIdFile, tgzFile):
        # only upload if requested
        if not self.__upload:
            return ""

        # only upload tools if built in sandbox
        if step.doesProvideTools() and (step.getSandbox() is None):
            return ""

        # upload with curl if file does not exist yet on server
        return "\n" + textwrap.dedent("""\
            # upload artifact
            cd $WORKSPACE
            BOB_UPLOAD_URL="{URL}/$(hexdump -e '2/1 "%02x/" 14/1 "%02x"' {BUILDID}).tgz"
            if ! curl --output /dev/null --silent --head --fail "$BOB_UPLOAD_URL" ; then
                curl -sSg -T {RESULT} "$BOB_UPLOAD_URL" || echo Upload failed: $?
            fi""".format(URL=self.__url, BUILDID=quote(buildIdFile), RESULT=quote(tgzFile)))

    def download(self, step, buildIdFile, tgzFile):
        # only download if requested
        if not self.__download:
            return ""

        # only download tools if built in sandbox
        if step.doesProvideTools() and (step.getSandbox() is None):
            return ""

        return "\n" + textwrap.dedent("""\
            BOB_DOWNLOAD_URL="{URL}/$(hexdump -e '2/1 "%02x/" 14/1 "%02x"' {BUILDID}).tgz"
            curl -sSg --fail -o {RESULT} "$BOB_DOWNLOAD_URL" || echo Download failed: $?
            """.format(URL=self.__url, BUILDID=quote(buildIdFile), RESULT=quote(tgzFile)))


def getArchiver(recipes):
    archiveSpec = recipes.archiveSpec()
    archiveBackend = archiveSpec.get("backend", "none")
    if archiveBackend == "file":
        return LocalArchive(archiveSpec)
    elif archiveBackend == "http":
        return SimpleHttpArchive(archiveSpec)
    elif archiveBackend == "none":
        return DummyArchive()
    else:
        raise BuildError("Invalid archive backend: "+archiveBackend)

