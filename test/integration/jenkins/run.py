#!/usr/bin/env python3

import base64
import http.cookiejar
import json
import os, os.path
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib
import urllib.parse
import urllib.request
import urllib.request
import urllib.response
import xml.etree.ElementTree

if sys.platform == "win32":
    bob = ["cmd", "/C", "bob"]
else:
    bob = ["bob"]

class JenkinsConnection:
    """Connection to a Jenkins server abstracting the REST API"""

    def __init__(self, url, username=None, passwd=""):
        self.__headers = { "Content-Type": "application/xml" }
        self.__root = url

        handlers = []

        # Handle cookies
        cookies = http.cookiejar.CookieJar()
        handlers.append(urllib.request.HTTPCookieProcessor(cookies))

        # handle authorization
        if username is not None:
            userPass = username + ":" + passwd
            self.__headers['Authorization'] = 'Basic ' + base64.b64encode(
                userPass.encode("utf-8")).decode("ascii")

        # remember basic settings
        self.__opener = urllib.request.build_opener(*handlers)

        # get CSRF token
        try:
            with self._send("GET", "crumbIssuer/api/xml") as response:
                resp = xml.etree.ElementTree.fromstring(response.read())
                crumb = resp.find("crumb").text
                field = resp.find("crumbRequestField").text
                self.__headers[field] = crumb
        except urllib.error.HTTPError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def _send(self, method, path, body=None, additionalHeaders={}):
        headers = self.__headers.copy()
        headers.update(additionalHeaders)
        req = urllib.request.Request(self.__root + path, data=body,
            method=method, headers=headers)
        return self.__opener.open(req)

    def triggerScmPoll(self, job):
        with self._send("POST", "job/" + job + "/polling") as response:
            response.read()

    def getBuildQueue(self):
        with self._send("GET", "queue/api/json") as response:
            q = json.loads(response.read())
            return q['items']

    def getIdleStatus(self):
        with self._send("GET", "computer/api/json") as response:
            q = json.loads(response.read())
            return q['computer'][0]['idle']

    def drainBuildQueue(self):
        for i in range(120):
            time.sleep(1)
            q = self.getBuildQueue()
            if q is None:
                print("[TEST]:", "Jenkins down!")
                sys.exit(2)
            if len(q) > 0:
                continue
            if not self.getIdleStatus():
                continue
            break
        else:
            print("[TEST]:", "Timeout waiting for build queue to drain!")
            sys.exit(2)

    def getLastBuildStatus(self, job):
        status = { 'result' : None }
        i = 30
        while status['result'] is None and i > 0:
            time.sleep(1)
            with self._send("GET", "job/" + job + "/lastBuild/api/json") as r:
                status = json.loads(r.read())
            i -= 1
        return status

    def getLastBuildResult(self, job, fn):
        status = self.getLastBuildStatus(job)
        for a in status['artifacts']:
            p = a['relativePath']
            if p.endswith(".tgz"): break
        else:
            return None

        url = "job/{}/{}/artifact/{}".format(job, status['number'], p)
        print("[TEST]:", "Download", url)
        with self._send("GET", url) as r:
            with tarfile.open(None, "r|*", fileobj=r, errorlevel=1) as tar:
                f = tar.next()
                while f is not None:
                    if f.name == ("content/" + fn):
                        return tar.extractfile(f).read()
                    f = tar.next()

    def getLastBuildLog(self, job):
        with self._send("GET", "job/" + job + "/lastBuild/consoleText") as r:
            return r.read()


def prepare(d):
    # reset state
    for i in os.listdir(d):
        if i.startswith('.bob-'):
            os.remove(os.path.join(d, i))

    # set proper configuration for platform
    with open(os.path.join(d, "config.yaml"), "w") as f:
        print('bobMinimumVersion: "0.20"', file=f)
        if sys.platform == "win32":
            print('scriptLanguage: PowerShell', file=f)
        else:
            print('scriptLanguage: bash', file=f)

def assertEqual(a, b):
    if a != b:
        print("[TEST]:", "Error: '{}' != '{}'".format(a, b))
        sys.exit(3)

def assertNotEqual(a, b):
    if a == b:
        print("[TEST]:", "Error: '{}' == '{}'".format(a, b))
        sys.exit(3)

###############################################################################


def testSimpleBuild(jc):
    prepare("testSimpleBuild")
    subprocess.run(bob + ["jenkins", "add", "local", "http://bob:test@localhost:8080/",
                    "-r", "root", "-p", "testSimpleBuild-"], check=True,
                    cwd="testSimpleBuild")
    subprocess.run(bob + ["jenkins", "push", "local"], check=True,
                   cwd="testSimpleBuild")

    jc.drainBuildQueue()
    status = jc.getLastBuildStatus("testSimpleBuild-root")
    print(jc.getLastBuildLog("testSimpleBuild-root"))
    assertEqual(status["result"], "SUCCESS")
    assertEqual(jc.getLastBuildResult("testSimpleBuild-root", "result.txt"), b'ok')

    subprocess.run(bob + ["jenkins", "prune", "local"], check=True,
                   cwd="testSimpleBuild")

def testGitModule(jc):
    """Build a project from a git module.

    Update the git module, trigger the Jenkins git hook and wait for the
    next build.
    """
    prepare("testGitModule")
    with tempfile.TemporaryDirectory() as gitDir:
        with open(os.path.join(gitDir, "result.txt"), "w") as f:
            f.write("foo")
        subprocess.run(["git", "init", "-b", "master", gitDir], check=True)
        subprocess.run(["git", "config", "user.email", "bob@test"], check=True, cwd=gitDir)
        subprocess.run(["git", "config", "user.name", "bob"], check=True, cwd=gitDir)
        subprocess.run(["git", "add", "result.txt"], check=True, cwd=gitDir)
        subprocess.run(["git", "commit", "-m", "init"], check=True, cwd=gitDir)

        subprocess.run(bob + ["jenkins", "add", "local", "http://bob:test@localhost:8080/",
                        "-r", "root", "-p", "testGitModule-",
                        "-DGITDIR=" + os.path.abspath(gitDir)],
                        check=True, cwd="testGitModule")
        subprocess.run(bob + ["jenkins", "push", "local"], check=True,
                       cwd="testGitModule")
        jc.drainBuildQueue()
        status = jc.getLastBuildStatus("testGitModule-root")
        print(jc.getLastBuildLog("testGitModule-root"))
        assertEqual(status["result"], "SUCCESS")
        assertEqual(jc.getLastBuildResult("testGitModule-root", "result.txt"), b'foo')

        with open(os.path.join(gitDir, "result.txt"), "w") as f:
            f.write("bar")
        subprocess.run(["git", "commit", "-a", "-m", "changed"], check=True,
                       cwd=gitDir)

        jc.triggerScmPoll("testGitModule-root")
        jc.drainBuildQueue()
        newStatus = jc.getLastBuildStatus("testGitModule-root")
        print(jc.getLastBuildLog("testGitModule-root"))
        assertEqual(newStatus["result"], "SUCCESS")
        assertNotEqual(status["number"], newStatus["number"])
        assertEqual(jc.getLastBuildResult("testGitModule-root", "result.txt"), b'bar')

def testSvnModule(jc):
    prepare("testSvnModule")
    with tempfile.TemporaryDirectory() as repoDir:
        subprocess.run(['svnadmin', 'create', 'bobSvnTest'], check=True,
                cwd=repoDir)
        testRepo = pathlib.Path(repoDir, "bobSvnTest").as_uri() + '/trunk'
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "result.txt"), "w") as f:
                f.write("baz")
            subprocess.run(['svn', 'import', testRepo, '-m', "Initial Import"],
                    check=True, cwd=tmp)

        subprocess.run(bob + ["jenkins", "add", "local", "http://bob:test@localhost:8080/",
                        "-r", "root", "-p", "testSvnModule-",
                        "-DSVNURL=" + testRepo],
                        check=True, cwd="testSvnModule")
        subprocess.run(bob + ["jenkins", "push", "local"], check=True,
                       cwd="testSvnModule")
        jc.drainBuildQueue()
        status = jc.getLastBuildStatus("testSvnModule-root")
        print(jc.getLastBuildLog("testSvnModule-root"))
        assertEqual(status["result"], "SUCCESS")
        assertEqual(jc.getLastBuildResult("testSvnModule-root", "result.txt"), b'baz')

        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(['svn', 'co', testRepo, tmp], check=True)
            with open(os.path.join(tmp, "result.txt"), "w") as f:
                f.write("asdf")
            subprocess.run(['svn', 'ci', '-m', "changed"],
                    check=True, cwd=tmp)

        jc.triggerScmPoll("testSvnModule-root")
        jc.drainBuildQueue()
        newStatus = jc.getLastBuildStatus("testSvnModule-root")
        print(jc.getLastBuildLog("testSvnModule-root"))
        assertEqual(newStatus["result"], "SUCCESS")
        assertNotEqual(status["number"], newStatus["number"])
        assertEqual(jc.getLastBuildResult("testSvnModule-root", "result.txt"), b'asdf')

def testCleanAfterBuild(jc):
    prepare("testCleanAfterBuild")
    subprocess.run(bob + ["jenkins", "add", "local", "http://bob:test@localhost:8080/",
                    "-o", "jobs.clean.post-build=always",
                    "-r", "root", "-p", "testCleanAfterBuild-"], check=True,
                    cwd="testCleanAfterBuild")
    subprocess.run(bob + ["jenkins", "push", "local"], check=True,
                   cwd="testCleanAfterBuild")

    jc.drainBuildQueue()
    status = jc.getLastBuildStatus("testCleanAfterBuild-root")
    print(jc.getLastBuildLog("testCleanAfterBuild-root"))
    assertEqual(status["result"], "SUCCESS")
    wsPath = jc.getLastBuildResult("testCleanAfterBuild-root", "result.txt").strip()
    assertEqual(os.path.exists(wsPath), False)

    subprocess.run(bob + ["jenkins", "prune", "local"], check=True,
                   cwd="testCleanAfterBuild")


TESTS = (
    ("Simple build", testSimpleBuild),
    ("Git module", testGitModule),
    ("Subversion module", testSvnModule),
    ("Clean after build", testCleanAfterBuild),
    # TODO: Multiple SCMs
)

###############################################################################

JENKINS_FILE = "jenkins.war"
JENKINS_URL = "https://get.jenkins.io/war-stable/latest/jenkins.war"
PLUGIN_MANAGER_FILE = "jenkins-plugin-manager.jar"
PLUGIN_MANAGER_URL = "https://github.com/jenkinsci/plugin-installation-manager-tool/releases/download/2.12.3/jenkins-plugin-manager-2.12.3.jar"

PLUGINS = [
    # Required by Bob
    "conditional-buildstep",
    "copyartifact",
    "git",
    "multiple-scms",
    "subversion",
    "ws-cleanup",

    # For automatic Jenkins setup
    "configuration-as-code",
]

CACHE = "cache"

def download(url, dest):
    if os.path.exists(dest): return
    print("[TEST]:", "Download", url)
    with urllib.request.urlopen(url) as response:
        with open(dest, "wb") as f:
            shutil.copyfileobj(response, f)

os.makedirs(CACHE, exist_ok=True)

# Download jenkins.war if not in cache
jenkins = os.path.join(CACHE, JENKINS_FILE)
download(JENKINS_URL, jenkins)

# Download jenkins-plugin-manager.jar if not in cache
pluginManager = os.path.join(CACHE, PLUGIN_MANAGER_FILE)
download(PLUGIN_MANAGER_URL, pluginManager)

# Fetch plugins unless in cache
plugins = os.path.join(CACHE, "plugins")
if any(not os.path.exists(os.path.join(plugins, i+".jpi")) for i in PLUGINS):
    print("[TEST]:", "Downloading plugins...")
    os.makedirs(plugins, exist_ok=True)
    subprocess.run(["java", "-jar", pluginManager, "--verbose",
                    "--war", jenkins, "-d", plugins, "--plugins"] + PLUGINS,
                    check=True)

# Run Jenkins and execute tests
with tempfile.TemporaryDirectory() as jenkinsHome:
    # copy plugins from cache
    shutil.copytree(plugins, os.path.join(jenkinsHome, "plugins"))
    try:
        env = os.environ.copy()
        env["JENKINS_HOME"] = os.path.abspath(jenkinsHome)
        jenkinsProc = subprocess.Popen(["java",
            "-Djenkins.install.runSetupWizard=false",
            "-Dhudson.plugins.git.GitSCM.ALLOW_LOCAL_CHECKOUT=true",
            "-Dcasc.jenkins.config=" + os.path.abspath("jenkins.yaml"),
            "-jar", jenkins,
            "--enable-future-java" ],
            env=env)
        print("[TEST]:", "Jenkins running as pid", jenkinsProc.pid, "in", jenkinsHome)

        print("[TEST]:", "Waiting for Jenkins to get ready...")
        for i in range(120):
            time.sleep(1)
            try:
                jc = JenkinsConnection("http://localhost:8080/", "bob", "test")
                if jc.getBuildQueue() == []: break
            except (urllib.error.URLError, OSError):
                pass
        else:
            print("[TEST]:", "Timeout waiting for Jenkins!")
            sys.exit(1)

        # Wait a bit more and re-open a new connection to get a CSRF crumb
        time.sleep(1)
        jc = JenkinsConnection("http://localhost:8080/", "bob", "test")

        # Execute tests
        for name, test in TESTS:
            print("[TEST]:", "Execute", name, "...")
            test(jc)

    finally:
        if jenkinsProc.poll() is None:
            print("[TEST]:", "Shutting down Jenkins...")
            jenkinsProc.terminate()
            jenkinsProc.wait(10)
