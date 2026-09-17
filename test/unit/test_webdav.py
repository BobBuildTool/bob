# Bob build tool
#
# SPDX-License-Identifier: GPL-3.0-or-later

from bob.webdav import WebDav, WebdavError, WebdavAlreadyExistsError, WebdavNotFoundError
from mocks.http_server import HttpServerMock
from tempfile import TemporaryDirectory, NamedTemporaryFile
from unittest import TestCase
from urllib.parse import urlparse
import os
import time

TEST_FILE="test.txt"
TEST_FILE2="test2.txt"
TEST_OUTPUT="testoutput"
TEST_OUTPUT_SIZE=10
TEST_PATH1="dir1"
TEST_PATH2="dir2"

class TestWebdav(TestCase):

    def setUp(self):
        self.__repodir = TemporaryDirectory()
        self.srvdir = self.__repodir.name
        self.clntdir = os.path.join(self.srvdir, "repo")
        os.mkdir(self.clntdir)
        self.webdav = self._startWebdav()
        super().setUp()

    def tearDown(self):
        self.__repodir.cleanup()
        super().tearDown()

    def _startWebdav(self, **kwargs):
        mock = HttpServerMock(self.srvdir, **kwargs)
        srv = mock.__enter__()
        self.addCleanup(mock.__exit__, None, None, None)
        return WebDav(urlparse("http://localhost:{}/repo/".format(srv.port)))

    def _serverPath(self, path):
        return os.path.join(self.clntdir, path)

    def _touch(self, path):
        with open(path, 'a'):
            pass

    def _checkEntries(self, entries, is_dir, paths):
        self.assertEqual(len(paths), len(entries))
        for entry in entries:
            self.assertEqual(entry["is_dir"], is_dir)
            self.assertIn(entry["path"], paths)

    def testUpload(self):
        """Uploading a new file creates it with the given content"""
        with NamedTemporaryFile() as file:
            file.write(TEST_OUTPUT.encode('utf-8'))
            self.webdav.upload(TEST_FILE, file, False)
            with open(self._serverPath(TEST_FILE)) as f:
                self.assertEqual(f.readline(), TEST_OUTPUT)

    def testUploadAlreadyExists(self):
        """Uploading without overwrite fails if the file already exists"""
        with NamedTemporaryFile() as file:
            file.write(TEST_OUTPUT.encode('utf-8'))
            self.webdav.upload(TEST_FILE, file, False)
            with self.assertRaises(WebdavAlreadyExistsError):
                self.webdav.upload(TEST_FILE, file, False)

    def testUploadOverwrite(self):
        """Uploading with overwrite replaces the existing content"""
        with NamedTemporaryFile() as file:
            file.write(TEST_OUTPUT.encode('utf-8'))
            self.webdav.upload(TEST_FILE, file, False)
            file.write(TEST_OUTPUT.encode('utf-8'))
            self.webdav.upload(TEST_FILE, file, True)
            with open(self._serverPath(TEST_FILE)) as f:
                self.assertEqual(f.readline(), TEST_OUTPUT + TEST_OUTPUT)

    def testUploadRetry(self):
        """A upload error is handled gracefully and can be retried"""
        webdav = self._startWebdav(retries=1)
        with NamedTemporaryFile() as file:
            file.write(TEST_OUTPUT.encode('utf-8'))
            # first try will result in an "internal server error"
            with self.assertRaises(WebdavError):
                webdav.upload(TEST_FILE, file, False)
            self.assertFalse(os.path.exists(self._serverPath(TEST_FILE)))
            # second upload will succeed
            webdav.upload(TEST_FILE, file, False)
            self.assertTrue(os.path.exists(self._serverPath(TEST_FILE)))

    def testUploadInterrupted(self):
        """An upload to an unresponsive server is handled gracefully"""
        webdav = self._startWebdav(noResponse=True)
        with NamedTemporaryFile() as file:
            file.write(TEST_OUTPUT.encode('utf-8'))
            with self.assertRaises(WebdavError):
                webdav.upload(TEST_FILE, file, False)

    def testDownload(self):
        """Downloading returns the full file content"""
        with open(self._serverPath(TEST_FILE), 'w') as f:
            f.write(TEST_OUTPUT)
        res = self.webdav.download(TEST_FILE)
        self.assertEqual(res.decode('utf-8'), TEST_OUTPUT)

    def testOpenDownload(self):
        """openDownload() yields a file-like object with the full content"""
        with open(self._serverPath(TEST_FILE), 'w') as f:
            f.write(TEST_OUTPUT + TEST_OUTPUT)
        with self.webdav.openDownload(TEST_FILE) as file:
            res = file.read()
        self.assertEqual(res.decode('utf-8'), TEST_OUTPUT + TEST_OUTPUT)

    def testDownloadRange(self):
        """offset and length restrict the downloaded byte range"""
        with open(self._serverPath(TEST_FILE), 'w') as f:
            f.write(TEST_OUTPUT + TEST_OUTPUT)
        res = self.webdav.download(TEST_FILE, offset=0, length=TEST_OUTPUT_SIZE)
        self.assertEqual(res.decode('utf-8'), TEST_OUTPUT)
        # data is "testoutputtestoutput"; offset 4, length 10 -> "outputtest"
        res = self.webdav.download(TEST_FILE, offset=4, length=TEST_OUTPUT_SIZE)
        self.assertEqual(res.decode('utf-8'), 'outputtest')

    def testDownloadNotFound(self):
        """Downloading a missing file raises WebdavNotFoundError"""
        with self.assertRaises(WebdavNotFoundError):
            self.webdav.download(TEST_FILE)

    def testDownloadServerError(self):
        """A single transient server error is not retried by the client itself"""
        webdav = self._startWebdav(retries=1)
        with self.assertRaises(WebdavError):
            webdav.download(TEST_FILE)

    def testDownloadInterrupted(self):
        """An interrupted download is handled gracefully"""
        webdav = self._startWebdav(noResponse=True)
        with self.assertRaises(WebdavError):
            webdav.download(TEST_FILE)

    def testExists(self):
        """exists() reports True for an existing file"""
        with open(self._serverPath(TEST_FILE), 'w') as f:
            f.write(TEST_OUTPUT)
        self.assertTrue(self.webdav.exists(TEST_FILE))

    def testExistsMissing(self):
        """exists() reports False for a missing file"""
        self.assertFalse(self.webdav.exists(TEST_FILE))

    def testExistsServerError(self):
        """A transient server error surfaces as WebdavError instead of being
        swallowed as a plain False"""
        webdav = self._startWebdav(retries=1, retryHead=True)
        with self.assertRaises(WebdavError):
            webdav.exists(TEST_FILE)
        # the injected failure is consumed by now; the real (missing) state
        # shows through on the next call
        self.assertFalse(webdav.exists(TEST_FILE))

    def testDelete(self):
        """delete() removes an existing file"""
        path = self._serverPath(TEST_FILE)
        with open(path, 'w') as f:
            f.write(TEST_OUTPUT)
        self.webdav.delete(TEST_FILE)
        self.assertFalse(os.path.exists(path))

    def testDeleteMissing(self):
        """Deleting a file that is already gone is not an error"""
        self.webdav.delete(TEST_FILE)

    def testDeleteServerError(self):
        """A transient server error is raised on the failing attempt but does
        not prevent a subsequent, successful delete"""
        path = self._serverPath(TEST_FILE)
        with open(path, 'w') as f:
            f.write(TEST_OUTPUT)
        webdav = self._startWebdav(retries=1)
        with self.assertRaises(WebdavError):
            webdav.delete(TEST_FILE)
        self.assertTrue(os.path.exists(path))
        webdav.delete(TEST_FILE)
        self.assertFalse(os.path.exists(path))

    def testMkdirDepth1(self):
        """Create a directory directly below the repository root"""
        path = self._serverPath(TEST_PATH1)
        self.webdav.mkdir(TEST_PATH1)
        self.assertTrue(os.path.exists(path))

    def testMkdirAlreadyExists(self):
        """Creating a directory that already exists is not an error"""
        path = self._serverPath(TEST_PATH1)
        self.webdav.mkdir(TEST_PATH1)
        self.webdav.mkdir(TEST_PATH1)
        self.assertTrue(os.path.exists(path))

    def testMkdirObstructed(self):
        """Creating a directory obstructed by a file fails"""
        # Create a file
        with open(self._serverPath(TEST_PATH1), "w"):
            pass

        remote_path = TEST_PATH1 + '/' + TEST_PATH2
        local_path = self._serverPath(os.path.join(TEST_PATH1, TEST_PATH2))

        with self.assertRaises(WebdavError) as cm:
            self.webdav.mkdir(remote_path)
        self.assertEqual("MKCOL 409 Conflict", str(cm.exception))
        self.assertFalse(os.path.exists(local_path))

    def testMkdirDepth2(self):
        """Create a non-existing two level directory hierarchy"""
        remote_path = TEST_PATH1 + '/' + TEST_PATH2
        local_path = self._serverPath(os.path.join(TEST_PATH1, TEST_PATH2))
        self.webdav.mkdir(remote_path)
        self.assertTrue(os.path.exists(local_path))

    def testListdirRoot(self):
        """Listing the root shows the top level directories"""
        os.makedirs(self._serverPath(os.path.join(TEST_PATH1, TEST_PATH2)))
        os.makedirs(self._serverPath(os.path.join(TEST_PATH2, TEST_PATH1)))
        res = self.webdav.listdir('/')
        self._checkEntries(res, True, [TEST_PATH1, TEST_PATH2])

    def testListdirSubdir(self):
        """Listing a sub directory shows its immediate children only"""
        os.makedirs(self._serverPath(os.path.join(TEST_PATH1, TEST_PATH2)))
        res = self.webdav.listdir('/' + TEST_PATH1)
        self._checkEntries(res, True, [TEST_PATH1 + '/' + TEST_PATH2])

    def testListdirFiles(self):
        """Listing a directory shows its files with their relative path"""
        path = self._serverPath(os.path.join(TEST_PATH1, TEST_PATH2))
        os.makedirs(path)
        self._touch(os.path.join(path, TEST_FILE))
        self._touch(os.path.join(path, TEST_FILE2))
        base_path = TEST_PATH1 + '/' + TEST_PATH2 + '/'
        res = self.webdav.listdir('/' + base_path)
        self._checkEntries(res, False, [base_path + TEST_FILE, base_path + TEST_FILE2])

    def testListdirEmpty(self):
        """Listing a non-existent directory returns no entries"""
        self.assertEqual(self.webdav.listdir('/'), [])

    def testStat(self):
        """listdir() reports the same length, mtime and etag as the filesystem"""
        path = self._serverPath(TEST_FILE)
        with open(path, 'w') as f:
            f.write(TEST_OUTPUT)
        stats = os.stat(path)

        entries = self.webdav.listdir("/")
        self.assertEqual(1, len(entries))
        res = entries[0]

        self.assertEqual(res['mdate'], time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(stats[8])))
        self.assertEqual(res['len'], stats[6])
        self.assertEqual(res['etag'], f'{stats[6]:x}-{stats[1]:x}-{stats[8]:x}')

    def testPartialDownloaderInitial(self):
        """get() initially returns exactly the requested number of bytes"""
        with open(self._serverPath(TEST_FILE), 'w') as f:
            f.write(TEST_OUTPUT + TEST_OUTPUT)
        pd = self.webdav.getPartialDownloader(TEST_FILE, 4)
        self.assertEqual(pd.get().decode('utf-8'), 'test')

    def testPartialDownloaderGrow(self):
        """more() extends the already downloaded data by the requested amount"""
        with open(self._serverPath(TEST_FILE), 'w') as f:
            f.write(TEST_OUTPUT + TEST_OUTPUT)
        pd = self.webdav.getPartialDownloader(TEST_FILE, 4)
        pd.more(2)
        self.assertEqual(pd.get().decode('utf-8'), 'testou')
        pd.more(4)
        self.assertEqual(pd.get().decode('utf-8'), TEST_OUTPUT)

    def testPartialDownloaderGrowBeyondEnd(self):
        """Requesting more data than remains only yields the leftover"""
        with open(self._serverPath(TEST_FILE), 'w') as f:
            f.write(TEST_OUTPUT + TEST_OUTPUT)
        pd = self.webdav.getPartialDownloader(TEST_FILE, 4)
        pd.more(6)
        pd.more(11)
        self.assertEqual(pd.get().decode('utf-8'), TEST_OUTPUT + TEST_OUTPUT)
