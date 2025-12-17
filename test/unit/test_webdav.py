from bob.webdav import WebDav, WebdavAlreadyExistsError, WebdavDownloadError, WebdavNotFoundError, WebdavUploadError
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

def GetWebdav(port):
    return WebDav(url=urlparse("http://localhost:{}/".format(port)), sslVerify=False)

class TestWebdav(TestCase):

    def setUp(self):
        self.__repodir = TemporaryDirectory()
        self.dir = self.__repodir.name
        super().setUp()

    def tearDown(self):
        self.__repodir.cleanup()
        super().tearDown()

    def testUpload(self):
        with HttpServerMock(repoPath=self.dir) as srv:
            webdav = GetWebdav(srv.port)
            with NamedTemporaryFile() as file:
                file.write(TEST_OUTPUT.encode('utf-8'))
                webdav.upload(TEST_FILE, file, False)
                # check if file got uploaded and check the content
                self.assertTrue(os.path.exists(os.path.join(self.dir, TEST_FILE)))
                with open(os.path.join(self.dir, TEST_FILE)) as f:
                    content = f.readline()
                    self.assertEqual(TEST_OUTPUT, content)
                # try to upload again should fail with WebdavAlreadyExistsError
                with self.assertRaises(WebdavAlreadyExistsError) as cm:
                    webdav.upload(TEST_FILE, file, False)
                # append same content to temp file
                file.write(TEST_OUTPUT.encode('utf-8'))
                webdav.upload(TEST_FILE, file, True)
                # content should be doubled in file now
                with open(os.path.join(self.dir, TEST_FILE)) as f:
                    content = f.readline()
                    self.assertEqual(TEST_OUTPUT + TEST_OUTPUT, content)

    def testUploadRetry(self):
        """A upload error is handled gracefully and can be retried"""
        with HttpServerMock(repoPath=self.dir, retries=1) as srv:
            webdav = GetWebdav(srv.port)
            with NamedTemporaryFile() as file:
                file.write(TEST_OUTPUT.encode('utf-8'))
                # first try will result in an "internal server error"
                with self.assertRaises(WebdavUploadError) as cm:
                    webdav.upload(TEST_FILE, file, False)
                self.assertFalse(os.path.exists(os.path.join(self.dir, TEST_FILE)))
                # second upload will succeed
                webdav.upload(TEST_FILE, file, False)
                self.assertTrue(os.path.exists(os.path.join(self.dir, TEST_FILE)))

    def testUploadInterrupted(self):
        """An upload to an unresponsive server is handled gracefully"""
        with HttpServerMock(repoPath=self.dir, noResponse=True) as srv:
            webdav = GetWebdav(srv.port)
            with NamedTemporaryFile() as file:
                file.write(TEST_OUTPUT.encode('utf-8'))
                with self.assertRaises(WebdavUploadError):
                    webdav.upload(TEST_FILE, file, False)

    def testDownload(self):
        with HttpServerMock(self.dir) as srv:
            # create file for server
            fpath = os.path.join(self.dir, TEST_FILE)
            with open(fpath, 'w') as f:
                f.write(TEST_OUTPUT)
            webdav = GetWebdav(srv.port)
            file = webdav.download(TEST_FILE)
            res = file.read()
            self.assertEqual(res.decode('utf-8'), TEST_OUTPUT)
            # add more data to file
            with open(fpath, 'a') as f:
                f.write(TEST_OUTPUT)
            file = webdav.download(TEST_FILE)
            res = file.read()
            self.assertEqual(res.decode('utf-8'), TEST_OUTPUT+TEST_OUTPUT)
            # test offset and length
            file = webdav.download(TEST_FILE, offset=0, length=TEST_OUTPUT_SIZE)
            res = file.read()
            self.assertEqual(res.decode('utf-8'), TEST_OUTPUT)
            # data is testoutputtestoutput, with offset 4 and length 10 we should get outputtest
            file = webdav.download(TEST_FILE, offset=4, length=TEST_OUTPUT_SIZE)
            res = file.read()
            self.assertEqual(res.decode('utf-8'), 'outputtest')
            # remove the file
            os.unlink(os.path.join(self.dir, TEST_FILE))
            # missing file will raise WebdavNotFoundError
            with self.assertRaises(WebdavNotFoundError) as cm:
                webdav.download(TEST_FILE)
        # with 1 retry, the download will fail initially
        with HttpServerMock(self.dir, retries=1) as srv:
            webdav = GetWebdav(srv.port)
            with self.assertRaises(WebdavDownloadError) as cm:
                webdav.download(TEST_FILE)

    def testDownloadInterrupted(self):
        """An interrupted download is handled gracefully"""
        with HttpServerMock(repoPath=self.dir, noResponse=True) as srv:
            webdav = GetWebdav(srv.port)
            with self.assertRaises(WebdavDownloadError):
                webdav.download(TEST_FILE)

    def testExists(self):
        with HttpServerMock(self.dir) as srv:
            # create file for server
            with open(os.path.join(self.dir, TEST_FILE), 'w') as f:
                f.write(TEST_OUTPUT)
            webdav = GetWebdav(srv.port)
            # file exists
            self.assertTrue(webdav.exists(TEST_FILE))
            # delete the file and exists shall return false
            os.unlink(os.path.join(self.dir, TEST_FILE))
            self.assertFalse(webdav.exists(TEST_FILE))
        # with 1 retry, exists will fail initially
        with HttpServerMock(self.dir, retries=1, retryHead=True) as srv:
            webdav = GetWebdav(srv.port)
            # file exists
            with self.assertRaises(WebdavUploadError) as cm:
                webdav.exists(TEST_FILE)
            self.assertFalse(webdav.exists(TEST_FILE))

    def testDelete(self):
        with HttpServerMock(self.dir) as srv:
            # create file for server
            path = os.path.join(self.dir, TEST_FILE)
            with open(path, 'w') as f:
                f.write(TEST_OUTPUT)
            self.assertTrue(os.path.exists(path))
            webdav = GetWebdav(srv.port)
            webdav.delete(TEST_FILE)
            # file should be deleted
            self.assertFalse(os.path.exists(path))
            # deleting again should not cause any problems
            webdav.delete(TEST_FILE)
            self.assertFalse(os.path.exists(path))
        with HttpServerMock(self.dir, retries=1) as srv:
            path = os.path.join(self.dir, TEST_FILE)
            with open(path, 'w') as f:
                f.write(TEST_OUTPUT)
            self.assertTrue(os.path.exists(path))
            webdav = GetWebdav(srv.port)
            # first attempt fails, so WebdavDownloadError is rasied
            with self.assertRaises(WebdavDownloadError) as cm:
                webdav.delete(TEST_FILE)
            self.assertTrue(os.path.exists(path))
            # second attempt will succeed
            webdav.delete(TEST_FILE)
            # file should be deleted
            self.assertFalse(os.path.exists(path))

    def testMkdir(self):
        with HttpServerMock(self.dir) as srv:
            ## depth 1
            path = os.path.join(self.dir, TEST_PATH1)
            webdav = GetWebdav(srv.port)
            webdav.mkdir(TEST_PATH1)
            self.assertTrue(os.path.exists(path))
            ## path already exists, no error occurs
            webdav.mkdir(TEST_PATH1)
            self.assertTrue(os.path.exists(path))
            os.rmdir(path)
            ## depth 2
            remote_path = TEST_PATH1 + '/' + TEST_PATH2
            path = os.path.join(self.dir, TEST_PATH1, TEST_PATH2)
            webdav = GetWebdav(srv.port)
            # should raise exception with error code 409, because we have a depth of 2, but only request 1
            with self.assertRaises(WebdavUploadError) as cm:
                webdav.mkdir(remote_path)
            self.assertEqual("MKCOL 409 Conflict", str(cm.exception))
            self.assertFalse(os.path.exists(path))
            # depth 2, so it will create the parent dir here, too
            webdav.mkdir(remote_path, 2)
            self.assertTrue(os.path.exists(path))
            os.rmdir(path)

    def testListdir(self):
        def checkEntries(entries, is_dir, paths):
            def checkyEntry(entry, is_dir, paths):
                return entry["is_dir"] == is_dir and entry["path"] in paths
            self.assertEqual(len(paths), len(entries))
            [self.assertTrue(checkyEntry(x, is_dir, paths)) for x in entries]

        def touch(path):
            with open(path,'a') as f:
                pass

        with HttpServerMock(self.dir) as srv:
            # create some simple directory structure
            path1 = os.path.join(self.dir, TEST_PATH1, TEST_PATH2)
            path2 = os.path.join(self.dir, TEST_PATH2, TEST_PATH1)
            os.makedirs(path1)
            os.makedirs(path2)
            touch(os.path.join(path1, TEST_FILE))
            touch(os.path.join(path1, TEST_FILE2))
            touch(os.path.join(path2, TEST_FILE))
            # check if listdir returns the proper content
            webdav = GetWebdav(srv.port)
            res = webdav.listdir('/')
            checkEntries(res, True, [TEST_PATH1, TEST_PATH2])
            res = webdav.listdir('/' + TEST_PATH1)
            checkEntries(res, True, [TEST_PATH1 + '/' + TEST_PATH2])
            base_path = TEST_PATH1 + '/' + TEST_PATH2 + '/'
            res = webdav.listdir('/' + base_path)
            checkEntries(res, False, [base_path + TEST_FILE, base_path + TEST_FILE2 ])
            base_path = TEST_PATH2 + '/' + TEST_PATH1 + '/'
            res = webdav.listdir('/' + base_path)
            checkEntries(res, False, [base_path + TEST_FILE])

            self.__repodir.cleanup()
            self.assertTrue(len(webdav.listdir('/')) == 0)

    def testStat(self):
        with HttpServerMock(self.dir) as srv:
            webdav = GetWebdav(srv.port)
            path = os.path.join(self.dir, TEST_FILE)
            # create file
            with open(os.path.join(path), 'a') as f:
                f.write(TEST_OUTPUT)
            stats = os.stat(path)
            res = webdav.stat(TEST_FILE)
            # compare file stats with returned webdav stats
            self.assertEqual(res['mdate'], time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(stats[8])))
            self.assertEqual(res['len'], stats[6])
            self.assertEqual(res['etag'], f'{stats[6]:x}-{stats[1]:x}-{stats[8]:x}')
            # modify file at a later time point
            time.sleep(1)
            with open(os.path.join(path), 'a') as f:
                f.write(TEST_OUTPUT)
            res2 = webdav.stat(TEST_FILE)
            # new webdav stats should be different
            self.assertNotEqual(res['mdate'], res2['mdate'])
            self.assertNotEqual(res['len'], res2['len'])
            self.assertNotEqual(res['etag'], res2['etag'])
            # stat on a non-existing file should return None
            os.unlink(path)
            self.assertIsNone(webdav.stat(TEST_FILE))

    def testPartialDownloader(self):
        with HttpServerMock(self.dir) as srv:
            webdav = GetWebdav(srv.port)
            # create file for server
            fpath = os.path.join(self.dir, TEST_FILE)
            with open(fpath, 'w') as f:
                f.write(TEST_OUTPUT + TEST_OUTPUT)
            pd = webdav.getPartialDownloader(TEST_FILE, 4)
            # first 4 bytes should be 'test'
            self.assertEqual(pd.get().decode('utf-8'), 'test')
            # getting 2 more bytes and we should get 'testou'
            pd.more(2)
            self.assertEqual(pd.get().decode('utf-8'), 'testou')
            pd.more(4)
            self.assertEqual(pd.get().decode('utf-8'), TEST_OUTPUT)
            # requesting more than what we should have left will only give us the leftover
            pd.more(11)
            self.assertEqual(pd.get().decode('utf-8'), TEST_OUTPUT + TEST_OUTPUT)
