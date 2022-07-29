from bob.archive_access import BaseArchiveAccess

from artifactory import ArtifactoryPath
import os
import tempfile
import datetime
import calendar
import struct

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class Artifactory(BaseArchiveAccess):
    def __init__(self):
        self.__url = "https://artifactory/bobs_cache"
        print("Using Artifactory Archive @ " + self.__url)

    def get(self, path):
        out = tempfile.NamedTemporaryFile("wb", delete=False)
        try:
            archive = ArtifactoryPath(self.__url + path, verify=False)
            with archive.open() as fd:
                out.write(fd.read())

        except Exception as e:
            logging.error(traceback.format_exc())
        out.close()
        return out.name

    def removeTmp(self, tmp):
        # remove the tmp file
        if tmp is not None and os.path.exists(tmp):
            os.unlink(tmp)

    def listdir(self, path):
        if path != ".":
            base = self.__url + path
        else:
            base = self.__url
        if not base.endswith("/"):
            base += "/"
        self.__path = ArtifactoryPath(base, verify=False)
        ret = [ str(p).replace(base, "") for p in self.__path ]
        return ret

    def binStat(self, path):
        archive = ArtifactoryPath(self.__url + path, verify=False)
        # Get FileStat
        stat = archive.stat()
        ctime = calendar.timegm(stat.ctime.timetuple())
        mtime = calendar.timegm(stat.mtime.timetuple())
        size = stat.size
        archive = ArtifactoryPath(self.__url + path, verify=False)
        return struct.pack('=qqQ64s', ctime, mtime, stat.size, bytes(stat.sha256, 'utf-8'))

    def unlink(self, path):
        archive = ArtifactoryPath(self.__url + path, verify=False)
        if archive.exists():
            archive.unlink()

    def getSize(self,path):
        archive = ArtifactoryPath(self.__url + path, verify=False)
        if archive.exists():
            return  archive.stat().size

ArtifactoryAccess = Artifactory()

manifest = {
    'apiVersion' : "0.21",
    'archiveAccessors' : {
        'Artifactory' : ArtifactoryAccess
    }
}
