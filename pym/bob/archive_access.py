class BaseArchiveAccess:
    """Base class for Archive Access handlers.
    """
    def get(self, path):
        """Get the package 'path' from the archive.
        Return the path the a local accessable archive file."""
        return ""
    def removeTmp(self, path):
        """Remove the temporary file returned by 'get'"""
        return None
    def listdir(self, path):
        """Return a list of directory entries"""
        return None
    def getSize(self,path):
        """Return the file size (in bytes) for 'path'"""
        return None
    def unlink(self, path):
        """Unlink 'path' from archive"""
        return None
    def binStat(self, path):
        """Return binary stat for 'path'"""
        return None
