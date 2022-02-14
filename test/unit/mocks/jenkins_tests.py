from .jenkins_mock import JenkinsMock
from bob.utils import asHexStr, removePath
import os, os.path
import shlex
import shutil
import tarfile
import tempfile
import textwrap
import yaml
import io
import gzip
import json

from bob.cmds.jenkins.jenkins import doJenkins
from bob.state import finalize

class ArtifactExtractor:
    def __init__(self, tgz):
        self.tgz = tgz

    def __enter__(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        with tarfile.open(self.tgz, "r:gz", errorlevel=1) as tar:
            f = tar.next()
            while f is not None:
                if f.name.startswith("content/"):
                    if f.islnk():
                        assert f.linkname.startswith("content/")
                        f.linkname = f.linkname[8:]
                    f.name = f.name[8:]
                    tar.extract(f, self.tmpdir.name)
                f = tar.next()
        return self.tmpdir.name

    def __exit__(self, exc_type, exc_value, traceback):
        self.tmpdir.cleanup()

def extractAudit(tgz):
    with tarfile.open(tgz, "r:gz", errorlevel=1) as tar:
        with tar.extractfile("meta/audit.json.gz") as f:
            with gzip.open(f, 'rb') as gzf:
                return json.load(io.TextIOWrapper(gzf, encoding='utf8'))

class JenkinsTests():
    def executeBobJenkinsCmd(self, args):
        doJenkins(shlex.split(args), self.cwd)

    def setUp(self):
        self.oldCwd = os.getcwd()
        self.jenkinsMock = JenkinsMock()
        self.jenkinsMock.start_mock_server()
        self.cwd = tempfile.mkdtemp()
        os.chdir(self.cwd)
        self.archive = tempfile.TemporaryDirectory()
        self.writeDefault({ "archive" : { "backend" : "file",
                                          "path" : self.archive.name }})
        self.writeConfig({ "bobMinimumVersion" : "0.20" })
        self.executeBobJenkinsCmd("add test http://localhost:{} -r root"
                                    .format(self.jenkinsMock.getServerPort()))

    def tearDown(self):
        self.jenkinsMock.stop_mock_server()
        finalize()
        os.chdir(self.oldCwd)
        removePath(self.cwd)
        self.archive.cleanup()

    def writeRecipe(self, name, content, layer=[]):
        path = os.path.join("",
            *(os.path.join("layers", l) for l in layer),
            "recipes")
        if path: os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, name+".yaml"), "w") as f:
            f.write(textwrap.dedent(content))

    def writeClass(self, name, content, layer=[]):
        path = os.path.join("",
            *(os.path.join("layers", l) for l in layer),
            "classes")
        if path: os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, name+".yaml"), "w") as f:
            f.write(textwrap.dedent(content))

    def writeConfig(self, content, layer=[]):
        path = os.path.join("", *(os.path.join("layers", l) for l in layer))
        if path: os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "config.yaml"), "w") as f:
            f.write(yaml.dump(content))

    def writeDefault(self, content, layer=[]):
        path = os.path.join("", *(os.path.join("layers", l) for l in layer))
        if path: os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "default.yaml"), "w") as f:
            f.write(yaml.dump(content))

    def getJobResult(self, name):
        # Archived in Jenkins master?
        ret = self.jenkinsMock.getJobResult(name)
        if ret is not None:
            return ArtifactExtractor(ret)

        # Fetch from archive
        bid = self.jenkinsMock.getJobBuildId(name)
        self.assertNotEqual(bid, None)
        bid = asHexStr(bid)
        tgz = os.path.join(self.archive.name, bid[0:2], bid[2:4],
                           bid[4:] + "-1.tgz")
        self.assertTrue(os.path.exists(tgz))
        return ArtifactExtractor(tgz)

    def getJobAudit(self, name):
        # Archived in Jenkins master?
        ret = self.jenkinsMock.getJobResult(name)
        if ret is not None:
            return extractAudit(ret)

        # Fetch from archive
        bid = self.jenkinsMock.getJobBuildId(name)
        self.assertNotEqual(bid, None)
        bid = asHexStr(bid)
        tgz = os.path.join(self.archive.name, bid[0:2], bid[2:4],
                           bid[4:] + "-1.tgz")
        self.assertTrue(os.path.exists(tgz))
        return extractAudit(tgz)
