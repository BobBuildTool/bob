from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread, RLock
from bob.utils import emptyDirectory
import http.client
import json
import os.path
import re
import shutil
import socket
import subprocess
import tempfile
import shlex
import xml.etree.ElementTree as ET
import urllib.parse

CRUMBDATA=b"""
<defaultCrumbIssuer>
<crumb>0123456789abcdef</crumb><crumbRequestField>.crumb</crumbRequestField>
</defaultCrumbIssuer>
"""
PLUGINS=b"""
{"plugins":[{"active":True,"backupVersion":"1.3.1","bundled":False,"deleted":False,"dependencies":[{},{},{},{},{},{},{},{},{}],"downgradable":True,"enabled":True,"hasUpdate":True,"longName":"conditional-buildstep","pinned":False,"shortName":"conditional-buildstep","supportsDynamicLoad":"MAYBE","url":"https://wiki.jenkins-ci.org/display/JENKINS/Conditional+BuildStep+Plugin","version":"1.3.3"},{"active":True,"backupVersion":"2.2.2","bundled":False,"deleted":False,"dependencies":[{},{},{},{},{},{},{},{},{},{},{}],"downgradable":True,"enabled":True,"hasUpdate":True,"longName":"Jenkins GIT plugin","pinned":False,"shortName":"git","supportsDynamicLoad":"MAYBE","url":"http://wiki.jenkins-ci.org/display/JENKINS/Git+Plugin","version":"2.2.7"},{"active":True,"backupVersion":"2.4","bundled":True,"deleted":False,"dependencies":[{},{},{},{},{},{},{},{},{}],"downgradable":True,"enabled":True,"hasUpdate":True,"longName":"Jenkins Subversion Plug-in","pinned":True,"shortName":"subversion","supportsDynamicLoad":"MAYBE","url":"http://wiki.jenkins-ci.org/display/JENKINS/Subversion+Plugin","version":"2.4.4"},{"active":True,"backupVersion":"2.4","bundled":True,"deleted":False,"dependencies":[{},{},{},{},{},{},{},{},{}],"downgradable":True,"enabled":True,"hasUpdate":True,"longName":"Copy Artifact Plug-in","pinned":True,"shortName":"copyartifact","supportsDynamicLoad":"MAYBE","url":"","version":"1.2.3"},{"active":True,"backupVersion":"2.4","bundled":True,"deleted":False,"dependencies":[{},{},{},{},{},{},{},{},{}],"downgradable":True,"enabled":True,"hasUpdate":True,"longName":"Jenkins Multiple SCMs plugin","pinned":True,"shortName":"multiple-scms","supportsDynamicLoad":"MAYBE","url":"","version":"1.2.3"},{"active":True,"backupVersion":"2.4","bundled":True,"deleted":False,"dependencies":[{},{},{},{},{},{},{},{},{}],"downgradable":True,"enabled":True,"hasUpdate":True,"longName":"Jenkins Workspace Cleanup Plugin","pinned":True,"shortName":"ws-cleanup","supportsDynamicLoad":"MAYBE","url":"","version":"1.2.3"}]}
"""

CONFIG_XML_RE = re.compile(r'^/job/([a-zA-Z0-9-_]+)/config\.xml$')
DELETE_RE = re.compile(r"^/job/([a-zA-Z0-9-_]+)/doDelete$")
SCHEDULE_RE = re.compile(r"^/job/([a-zA-Z0-9-_]+)/build$")
ENABLE_RE = re.compile(r"^/job/([a-zA-Z0-9-_]+)/enable$")
DISABLE_RE = re.compile(r"^/job/([a-zA-Z0-9-_]+)/disable$")
DESCRIPTION_RE = re.compile(r"^/job/([a-zA-Z0-9-_]+)/description$")

class JenkinsError(Exception):
    pass

class MockServerRequestHandler(BaseHTTPRequestHandler):

    def __replyString(self, s):
        # TODO: encoding? content-length?
        self.send_response(HTTPStatus.OK)
        self.end_headers()
        self.wfile.write(s)

    def __replyJob(self, name):
        job = self.server.getJob(name)
        if job:
            self.send_response(HTTPStatus.OK)
            self.end_headers()
            self.wfile.write(job)
        else:
            self.send_response(404)
            self.end_headers()

    def __createJob(self, name, body):
        self.send_response(self.server.createJob(name, body))
        self.end_headers()

    def __deleteJob(self, name):
        if self.server.deleteJob(name):
            self.send_response(HTTPStatus.FOUND)
        else:
            self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()

    def __updateJob(self, name, body):
        self.send_response(self.server.updateJob(name, body))
        self.end_headers()

    def __scheduleJob(self, name):
        if self.server.scheduleJob(name):
            self.send_response(HTTPStatus.OK)
        else:
            self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()

    def __enableJob(self, name):
        if self.server.enableJob(name):
            self.send_response(HTTPStatus.OK)
        else:
            self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()

    def __disableJob(self, name):
        if self.server.disableJob(name):
            self.send_response(HTTPStatus.OK)
        else:
            self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()

    def __setJobDescription(self, name, body):
        q = urllib.parse.parse_qs(body.decode('ascii'))
        print(q)
        if "description" not in q:
            self.send_response(HTTPStatus.BAD_REQUEST)
        elif self.server.setJobDescription(name, q["description"][0]):
            self.send_response(HTTPStatus.OK)
        else:
            self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()


    def do_GET(self):
        if self.path == '/crumbIssuer/api/xml':
            self.__replyString(CRUMBDATA)
        elif self.path.startswith('/pluginManager/api/python'):
            self.__replyString(PLUGINS)
        elif CONFIG_XML_RE.match(self.path):
            m = CONFIG_XML_RE.match(self.path)
            if m:
                self.__replyJob(m.group(1))
            else:
                self.send_response(404)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get('content-length',0))
        body = self.rfile.read(length)

        if self.path.startswith("/createItem?name="):
            self.__createJob(self.path[17:], body)
        elif DELETE_RE.match(self.path):
            self.__deleteJob(DELETE_RE.match(self.path).group(1))
        elif CONFIG_XML_RE.match(self.path):
            self.__updateJob(CONFIG_XML_RE.match(self.path).group(1), body)
        elif SCHEDULE_RE.match(self.path):
            self.__scheduleJob(SCHEDULE_RE.match(self.path).group(1))
        elif ENABLE_RE.match(self.path):
            self.__enableJob(ENABLE_RE.match(self.path).group(1))
        elif DISABLE_RE.match(self.path):
            self.__disableJob(DISABLE_RE.match(self.path).group(1))
        elif DESCRIPTION_RE.match(self.path):
            self.__setJobDescription(DESCRIPTION_RE.match(self.path).group(1), body)
        else:
            self.send_response(404)
            self.end_headers()

    #def log_message(self, format, *args):
    #    return

def boolFromStr(s):
    if s.lower() == "true":
        return True
    elif s.lower() == "false":
        return False
    else:
        raise JenkinsError("no bool")

def builderFromNode(x, tag):
    if tag == "hudson.plugins.copyartifact.CopyArtifact":
        return CopyArtifact(x)
    elif tag == "org.jenkinsci.plugins.conditionalbuildstep.singlestep.SingleConditionalBuilder":
        return ConditionalBuilder(x)
    elif tag == "hudson.tasks.Shell":
        return ShellBuilder(x)
    else:
        raise JenkinsError("unsupported builder")

def scmFromNode(x):
    if x.attrib['class'] == "hudson.plugins.git.GitSCM":
        return GitScm(x)
    elif x.attrib['class'] == "hudson.scm.NullSCM":
        return NullScm()
    else:
        raise JenkinsError("unsupported scm")

class CopyArtifact:
    def __init__(self, x):
        self.__project = x.find('./project').text
        self.__files = [ i.strip() for i in x.find('./filter').text.split(' ') if i.strip() ]
        print("CopyArtifact:", self.__files)

    def run(self, server, workspace, env):
        fromWorkspace = server.getJobArtifacts(self.__project)
        for f in self.__files:
            print("CopyArtifact.run", os.path.join(fromWorkspace, f), workspace)
            shutil.copy2(os.path.join(fromWorkspace, f), workspace)

class ShellCondition:
    def __init__(self, x, interpreter, ending):
        self.__condition = x.find('./command').text
        self.__interpreter = interpreter
        self.__ending = ending

    def run(self, server, workspace, env):
        print("ShellCondition.run:", self.__condition, env, workspace)
        args = self.__interpreter
        condition = self.__condition
        if condition.startswith("#!"):
            interpreter, _, _ = condition.partition('\n')
            args = shlex.split(interpreter[2:])
        with tempfile.TemporaryDirectory() as tmpDir:
            fn = os.path.join(tmpDir, "condition." + self.__ending)
            with open(fn, 'w') as f:
                f.write(condition)
            args.append(fn)
            r = subprocess.run(args, cwd=workspace, env=env).returncode
        if r == 0:
            print("ShellCondition.run", True)
            return True
        elif r == 1:
            print("ShellCondition.run", False)
            return False
        else:
            raise JenkinsError("shell condition fail: " + str(r))

class ConditionalBuilder:
    def __init__(self, x):
        c = x.find('./condition')
        if c.attrib['class'] == "org.jenkins_ci.plugins.run_condition.contributed.ShellCondition":
            self.__condition = ShellCondition(c, ["/bin/sh"], "sh")
        elif c.attrib['class'] == "org.jenkins_ci.plugins.run_condition.contributed.BatchFileCondition":
            self.__condition = ShellCondition(c, ["cmd", "/C"], "cmd")
        else:
            raise JenkinsError("unsupported condition")
        b = x.find('./buildStep')
        self.__buildStep = builderFromNode(b, b.attrib['class'])

    def run(self, server, workspace, env):
        if self.__condition.run(server, workspace, env):
            print("ConditionalBuilder.run", True)
            self.__buildStep.run(server, workspace, env)
        else:
            print("ConditionalBuilder.run", False)

class ShellBuilder:
    def __init__(self, x):
        self.__command = x.find('./command').text

    def run(self, server, workspace, env):
        args = ["/bin/sh"]
        cmd = self.__command
        if cmd.startswith("#!"):
            interpreter, _, _ = cmd.partition('\n')
            args = shlex.split(interpreter[2:])
        print("ShellBuilder.run", args)
        with tempfile.TemporaryDirectory() as tmpDir:
            fn = os.path.join(tmpDir, "spec")
            with open(fn, 'w') as f:
                f.write(cmd)
            args.append(fn)
            subprocess.run(args, cwd=workspace, check=True, env=env)

class NullScm:
    def run(self, server, workspace):
        pass

class GitScm:
    def __init__(self, x):
        self.__url = x.find('./userRemoteConfigs/hudson.plugins.git.UserRemoteConfig/url').text
        self.__ref = x.find('./branches/hudson.plugins.git.BranchSpec/name').text
        self.__dir = x.find('./extensions/hudson.plugins.git.extensions.impl.RelativeTargetDirectory/relativeTargetDir').text

    def run(self, server, workspace):
        d = os.path.join(workspace, self.__dir)
        if not os.path.exists(d):
            subprocess.run(["git", "init", d], check=True)
            subprocess.run(["git", "remote", "add", "origin", self.__url], cwd=d, check=True)
        else:
            subprocess.run(["git", "remote", "set-url", "origin", self.__url], cwd=d, check=True)
        subprocess.run(["git", "fetch", "origin"], cwd=d, check=True)
        if self.__ref.startswith("refs/heads/"):
            subprocess.run(["git", "checkout", "remotes/origin/" + self.__ref[11:]], cwd=d, check=True)
        else:
            subprocess.run(["git", "checkout", self.__ref], cwd=d, check=True)

class Job:
    def __init__(self, name, config):
        self.name = name
        self.scheduled = False
        self.build = 1
        self.setXml(config)

    def getXml(self):
        return self.__config

    def setXml(self, config):
        # TODO: atomic update
        x = ET.fromstring(config)
        self.__disabled = boolFromStr(x.find('./disabled').text)
        self.__clean = x.find('./buildWrappers/hudson.plugins.ws__cleanup.PreBuildCleanup') is not None
        self.__scm = scmFromNode(x.find('./scm'))
        self.__builders = [ builderFromNode(i, i.tag) for i in x.findall('./builders/') ]
        upstream = x.find('./triggers/jenkins.triggers.ReverseBuildTrigger/upstreamProjects')
        self.__upstream = [] if upstream is None else [ i.strip() for i in upstream.text.split(',') ]
        publish = x.find('./publishers/hudson.tasks.ArtifactArchiver/artifacts')
        self.__publish = [] if publish is None else publish.text.split(',')
        if self.__disabled:
            self.scheduled = False
        print(self.name)
        print("  Upstream:", self.__upstream)
        print("  Publish:", self.__publish)

        self.__config = config

    def setEnabled(self, enable):
        x = ET.fromstring(self.__config)
        x.find('./disabled').text = "false" if enable else "true"
        self.__config = ET.tostring(x, encoding="UTF-8")
        if not enable:
            self.scheduled = False
        self.__disabled = not enable

    def setDescription(self, description):
        x = ET.fromstring(self.__config)
        x.find('./description').text = description
        self.__config = ET.tostring(x, encoding="UTF-8")

    def getEnabled(self):
        return not self.__disabled

    def getUpstreamJobs(self):
        return self.__upstream

    def getResult(self):
        for i in self.__publish:
            if i.endswith(".tgz"): return i
        return None

    def getBuildId(self):
        for i in self.__publish:
            if i.endswith(".buildid"): return i
        return None

    def schedule(self):
        if not self.__disabled:
            self.scheduled = True

    def run(self, server, workspace, archive, env):
        url = env["JENKINS_URL"]
        build = self.build
        self.build += 1

        env = env.copy()
        env.update({
            "BUILD_TAG" : "jenkins-{}-{}".format(self.name, build),
            "BUILD_URL" : url + "job/" + self.name + "/" + str(build) + "/",
            "WORKSPACE" : workspace,
        })

        if self.__clean:
            print("Clean workspace")
            emptyDirectory(workspace)
        self.__scm.run(server, workspace)
        for b in self.__builders:
            b.run(server, workspace, env)
        for i in self.__publish:
            shutil.copy(os.path.join(workspace, i), archive)

class StoppableHttpServer (HTTPServer):
    address_family = socket.AF_INET6
    def __init__(self, address, handler):
        super().__init__(address, handler)
        self.__mutex = RLock()
        self.__jobs = {}

    # Decorator to grab mutex while method is executed
    def __synchronized(fn):
        def sync(self, *args, **kwargs):
            with self.__mutex:
                return fn(self, *args, **kwargs)
        return sync

    def serve(self):
        with tempfile.TemporaryDirectory() as home:
            self.__home = home
            print("JENKINS_HOME:", self.__home)
            self.serve_forever(poll_interval=0.1)

    @__synchronized
    def getJob(self, name):
        if name in self.__jobs:
            return self.__jobs[name].getXml()
        else:
            return None

    @__synchronized
    def createJob(self, name, config):
        if name in self.__jobs:
            return HTTPStatus.BAD_REQUEST

        try:
            self.__jobs[name] = Job(name, config)
            return HTTPStatus.CREATED
        except JenkinsError as e:
            print("createJob: error: " + str(e))
            return HTTPStatus.INTERNAL_SERVER_ERROR

    @__synchronized
    def updateJob(self, name, config):
        if name not in self.__jobs:
            return HTTPStatus.NOT_FOUND
        try:
            self.__jobs[name].setXml(config)
            return HTTPStatus.OK
        except JenkinsError as e:
            print("updateJob: error: " + str(e))
            return HTTPStatus.INTERNAL_SERVER_ERROR

    @__synchronized
    def deleteJob(self, name):
        if name not in self.__jobs:
            return False
        del self.__jobs[name]
        return True

    @__synchronized
    def enableJob(self, name):
        if name in self.__jobs:
            self.__jobs[name].setEnabled(True)
            return True
        else:
            return False

    @__synchronized
    def disableJob(self, name):
        if name in self.__jobs:
            self.__jobs[name].setEnabled(False)
            return True
        else:
            return False

    @__synchronized
    def setJobDescription(self, name, description):
        if name in self.__jobs:
            self.__jobs[name].setDescription(description)
            return True
        else:
            return False

    @__synchronized
    def scheduleJob(self, name):
        job = self.__jobs.get(name)
        if job:
            job.schedule()
            return True
        else:
            return False

    @__synchronized
    def run(self):
        env = os.environ.copy()
        env.update({
            "CI" : "true",
            "EXECUTOR_NUMBER" : "0",
            "NODE_NAME" : "built-in",
            "NODE_LABELS" : "",
            "JENKINS_HOME" : self.__home,
            "JENKINS_URL" : "http://localhost:{}/".format(self.server_address[1]),
        })

        # First get order of jobs
        order = self.__genBuildOrder()

        # Build jobs from downstream to upstream, triggering upstream builds
        for name in order:
            job = self.__jobs[name]
            if not job.scheduled: continue

            workspace = os.path.join(self.__home, "workspace", name)
            os.makedirs(workspace, exist_ok=True)
            artifacts = os.path.join(self.__home, "artifacts", name)
            os.makedirs(artifacts, exist_ok=True)
            emptyDirectory(artifacts)

            print("Run", name)
            job.run(self, workspace, artifacts, env)

            for i in self.__getTriggers(name):
                self.__jobs[i].schedule()

    @__synchronized
    def getJenkinsHome(self):
        return self.__home

    @__synchronized
    def getJobArtifacts(self, name):
        return os.path.join(self.__home, "artifacts", name)

    @__synchronized
    def getJobResult(self, name):
        res = self.__jobs[name].getResult()
        return res and os.path.join(self.getJobArtifacts(name), res)

    @__synchronized
    def getJobBuildId(self, name):
        res = self.__jobs[name].getBuildId()
        return res and os.path.join(self.getJobArtifacts(name), res)

    @__synchronized
    def getJobBuildNumber(self, name):
        return self.__jobs[name].build

    @__synchronized
    def getJobExists(self, name):
        return name in self.__jobs

    @__synchronized
    def getJobEnabled(self, name):
        return self.__jobs[name].getEnabled()

    @__synchronized
    def getJobConfig(self, name):
        return self.__jobs[name].getXml()

    @__synchronized
    def getJobs(self):
        return list(self.__jobs.keys())

    def __genBuildOrder(self):
        def visit(j, pending, processing, order, stack):
            if j in processing:
                raise ParseError("Jobs are cyclic: " + " -> ".join(stack))
            if j in pending:
                processing.add(j)
                for d in self.__jobs[j].getUpstreamJobs():
                    visit(d, pending, processing, order, stack + [d])
                pending.remove(j)
                processing.remove(j)
                order.append(j)

        order = []
        pending = set(self.__jobs.keys())
        processing = set()
        while pending:
            j = pending.pop()
            pending.add(j)
            visit(j, pending, processing, order, [j])

        return order

    def __getTriggers(self, upstream):
        ret = []
        for (name, job) in self.__jobs.items():
            if upstream in job.getUpstreamJobs():
                ret.append(name)
        return ret


class JenkinsMock():

    def start_mock_server(self):
        self.mock_server = StoppableHttpServer(('localhost', 0), MockServerRequestHandler)
        self.mock_server_thread = Thread(target=self.mock_server.serve)
        self.mock_server_thread.start()

    def stop_mock_server(self):
        self.mock_server.shutdown()
        self.mock_server_thread.join()
        self.mock_server.server_close()

    def getServerPort(self):
        return self.mock_server.server_address[1]

    def getJenkinsHome(self):
        return self.mock_server.getJenkinsHome()

    def run(self, force=[]):
        for name in force:
            self.mock_server.scheduleJob(name)
        self.mock_server.run()

    def getJobResult(self, name):
        return self.mock_server.getJobResult(name)

    def getJobBuildId(self, name):
        res = self.mock_server.getJobBuildId(name)
        with open(res, "rb") as f:
            return f.read()

    def getJobBuildNumber(self, name):
        return self.mock_server.getJobBuildNumber(name)

    def getJobExists(self, name):
        return self.mock_server.getJobExists(name)

    def getJobEnabled(self, name):
        return self.mock_server.getJobEnabled(name)

    def getJobConfig(self, name):
        return self.mock_server.getJobConfig(name)

    def getJobs(self):
        return self.mock_server.getJobs()

if __name__ == '__main__':
    server = StoppableHttpServer(('localhost', 8000), MockServerRequestHandler)
    server.serve()
