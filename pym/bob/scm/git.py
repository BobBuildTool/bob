# Bob build tool
# Copyright (C) 2016  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ..errors import ParseError, BuildError
from ..stringparser import isTrue, IfExpression
from ..tty import WarnOnce, stepAction, INFO, TRACE, WARNING
from ..utils import check_output, joinLines
from .scm import Scm, ScmAudit, ScmStatus, ScmTaint
from shlex import quote
from textwrap import dedent, indent
from xml.etree import ElementTree
import asyncio
import concurrent.futures
import hashlib
import locale
import os, os.path
import re
import schema
import subprocess

class GitScm(Scm):

    SCHEMA = schema.Schema({
        'scm' : 'git',
        'url' : str,
        schema.Optional('dir') : str,
        schema.Optional('if') : schema.Or(str, IfExpression),
        schema.Optional('branch') : str,
        schema.Optional('tag') : str,
        schema.Optional('commit') : str,
        schema.Optional('rev') : str,
        schema.Optional(schema.Regex('^remote-.*')) : str,
        schema.Optional('sslVerify') : bool,
    })
    REMOTE_PREFIX = "remote-"

    def __init__(self, spec, overrides=[], secureSSL=None):
        super().__init__(spec, overrides)
        self.__url = spec["url"]
        self.__branch = None
        self.__tag = None
        self.__commit = None
        self.__remotes = {}
        if "rev" in spec:
            rev = spec["rev"]
            if rev.startswith("refs/heads/"):
                self.__branch = rev[11:]
            elif rev.startswith("refs/tags/"):
                self.__tag = rev[10:]
            elif len(rev) == 40:
                self.__commit = rev
            else:
                raise ParseError("Invalid rev format: " + rev)
        self.__branch = spec.get("branch", self.__branch)
        self.__tag = spec.get("tag", self.__tag)
        self.__commit = spec.get("commit", self.__commit)
        if self.__commit:
            # validate commit
            if re.match("^[0-9a-f]{40}$", self.__commit) is None:
                raise ParseError("Invalid commit id: " + str(self.__commit))
        elif not self.__branch and not self.__tag:
            # nothing secified at all -> master branch
            self.__branch = "master"
        self.__dir = spec.get("dir", ".")
        # convert remotes into separate dictionary
        for key, val in spec.items():
            if key.startswith(GitScm.REMOTE_PREFIX):
                stripped_key = key[len(GitScm.REMOTE_PREFIX):] # remove prefix
                if stripped_key == "origin":
                    raise ParseError("Invalid remote name: " + stripped_key)
                self.__remotes.update({stripped_key : val})
        self.__sslVerify = spec.get('sslVerify', secureSSL)

    def getProperties(self):
        properties = super().getProperties()
        properties.update({
            'scm' : 'git',
            'url' : self.__url,
            'branch' : self.__branch,
            'tag' : self.__tag,
            'commit' : self.__commit,
            'dir' : self.__dir,
            'rev' : ( self.__commit if self.__commit else
                (("refs/tags/" + self.__tag) if self.__tag else
                    ("refs/heads/" + self.__branch))
            ),
            'sslVerify' : self.__sslVerify,
        })
        for key, val in self.__remotes.items():
            properties.update({GitScm.REMOTE_PREFIX+key : val})
        return properties

    async def invoke(self, invoker):
        # make sure the git directory exists
        if not os.path.isdir(invoker.joinPath(self.__dir, ".git")):
            await invoker.checkCommand(["git", "init", self.__dir])

        # setup and update remotes
        remotes = { "origin" : self.__url }
        remotes.update(self.__remotes)
        existingRemotes = await invoker.checkOutputCommand(["git", "remote"], cwd=self.__dir)
        for remote in existingRemotes.split("\n"):
            if remote in remotes:
                cfgUrl = remotes[remote]
                realUrl = await invoker.checkOutputCommand(
                    ["git", "ls-remote", "--get-url", remote], cwd=self.__dir)
                if cfgUrl != realUrl:
                    await invoker.checkCommand(["git", "remote", "set-url", remote, cfgUrl], cwd=self.__dir)
                del remotes[remote]

        # add remaining (new) remotes
        for remote,url in remotes.items():
            await invoker.checkCommand(["git", "remote", "add", remote, url], cwd=self.__dir)

        # relax security if requested
        if not self.__sslVerify:
            await invoker.checkCommand(["git", "config", "http.sslVerify", "false"], cwd=self.__dir)

        # do the checkout
        if self.__tag or self.__commit:
            refSpec = ["+refs/heads/*:refs/remotes/origin/*"]
            if self.__tag:
                refSpec.append("refs/tags/{0}:refs/tags/{0}".format(self.__tag))
            # checkout only if HEAD is invalid
            head = await invoker.callCommand(["git", "rev-parse", "--verify", "-q", "HEAD"],
                stdout=False, cwd=self.__dir)
            if head:
                await invoker.checkCommand(["git", "fetch", "origin"] + refSpec, cwd=self.__dir)
                await invoker.checkCommand(["git", "checkout", "-q",
                    self.__commit if self.__commit else "tags/"+self.__tag], cwd=self.__dir)
        else:
            await invoker.checkCommand(["git", "fetch", "-p", "origin"], cwd=self.__dir)
            if await invoker.callCommand(["git", "rev-parse", "--verify", "-q", "HEAD"],
                           stdout=False, cwd=self.__dir):
                # checkout only if HEAD is invalid
                await invoker.checkCommand(["git", "checkout", "-b", self.__branch,
                    "remotes/origin/"+self.__branch], cwd=self.__dir)
            elif (await invoker.checkOutputCommand(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=self.__dir)) == self.__branch:
                # pull only if on original branch
                await invoker.checkCommand(["git", "merge", "--ff-only", "refs/remotes/origin/"+self.__branch], cwd=self.__dir)
            else:
                invoker.warn("Not updating", self.__dir, "because branch was changed manually...")


    def asDigestScript(self):
        """Return forward compatible stable string describing this git module.

        The format is "url rev-spec dir" where rev-spec depends on the given reference.
        """
        if self.__commit:
            return self.__commit + " " + self.__dir
        elif self.__tag:
            return self.__url + " refs/tags/" + self.__tag + " " + self.__dir
        else:
            return self.__url + " refs/heads/" + self.__branch + " " + self.__dir

    def asJenkins(self, workPath, credentials, options):
        scm = ElementTree.Element("scm", attrib={
            "class" : "hudson.plugins.git.GitSCM",
            "plugin" : "git@2.2.7",
        })
        ElementTree.SubElement(scm, "configVersion").text = "2"

        userconfigs =  ElementTree.SubElement(
                ElementTree.SubElement(scm, "userRemoteConfigs"),
                "hudson.plugins.git.UserRemoteConfig")

        url = ElementTree.SubElement(userconfigs,
            "url")
        url.text = self.__url

        if credentials:
            credentialsId = ElementTree.SubElement(userconfigs,
                         "credentialsId")
            credentialsId.text = credentials

        branch = ElementTree.SubElement(
            ElementTree.SubElement(
                ElementTree.SubElement(scm, "branches"),
                "hudson.plugins.git.BranchSpec"),
            "name")
        if self.__commit:
            branch.text = self.__commit
        elif self.__tag:
            branch.text = "refs/tags/" + self.__tag
        else:
            branch.text = "refs/heads/" + self.__branch

        ElementTree.SubElement(scm, "doGenerateSubmoduleConfigurations").text = "false"
        ElementTree.SubElement(scm, "submoduleCfg", attrib={"class" : "list"})

        extensions = ElementTree.SubElement(scm, "extensions")
        ElementTree.SubElement(
            ElementTree.SubElement(extensions,
                "hudson.plugins.git.extensions.impl.RelativeTargetDirectory"),
            "relativeTargetDir").text = os.path.normpath(os.path.join(workPath, self.__dir))
        # remove untracked files and stale branches
        ElementTree.SubElement(extensions,
            "hudson.plugins.git.extensions.impl.CleanCheckout")
        ElementTree.SubElement(extensions,
            "hudson.plugins.git.extensions.impl.PruneStaleBranch")
        # set git clone options
        shallow = options.get("scm.git.shallow")
        timeout = options.get("scm.git.timeout")
        if shallow is not None or timeout is not None:
            co = ElementTree.SubElement(extensions,
                    "hudson.plugins.git.extensions.impl.CloneOption")
            if shallow is not None:
                try:
                    shallow = int(shallow)
                    if shallow < 0: raise ValueError()
                except ValueError:
                    raise BuildError("Invalid 'git.shallow' option: " + str(shallow))
                if shallow > 0:
                    ElementTree.SubElement(co, "shallow").text = "true"
                    ElementTree.SubElement(co, "noTags").text = "false"
                    ElementTree.SubElement(co, "reference").text = ""
                    ElementTree.SubElement(co, "depth").text = str(shallow)
                    ElementTree.SubElement(co, "honorRefspec").text = "false"

            if timeout is not None:
                try:
                    timeout = int(timeout)
                    if timeout < 0: raise ValueError()
                except ValueError:
                    raise BuildError("Invalid 'git.timeout' option: " + str(shallow))
                if timeout > 0:
                    ElementTree.SubElement(co, "timeout").text = str(timeout)

        if isTrue(options.get("scm.ignore-hooks", "0")):
            ElementTree.SubElement(extensions,
                "hudson.plugins.git.extensions.impl.IgnoreNotifyCommit")

        return scm

    def getDirectory(self):
        return self.__dir

    def isDeterministic(self):
        return bool(self.__tag) or bool(self.__commit)

    def hasJenkinsPlugin(self):
        return True

    def callGit(self, workspacePath, *args):
        cmdLine = ['git']
        cmdLine.extend(args)
        cwd = os.path.join(workspacePath, self.__dir)
        try:
            output = subprocess.check_output(cmdLine, cwd=cwd,
                universal_newlines=True, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError as e:
            raise BuildError("git error:\n Directory: '{}'\n Command: '{}'\n'{}'".format(
                cwd, " ".join(cmdLine), e.output.rstrip()))
        except OSError as e:
            raise BuildError("Error calling git: " + str(e))
        return output.strip()

    def status(self, workspacePath):
        status = ScmStatus()
        try:
            onCorrectBranch = False
            onTag = False
            output = self.callGit(workspacePath, 'ls-remote' ,'--get-url')
            if output != self.__url:
                status.add(ScmTaint.switched,
                    "> URL: configured: '{}', actual: '{}'".format(self.__url, output))

            if self.__commit:
                output = self.callGit(workspacePath, 'rev-parse', 'HEAD')
                if output != self.__commit:
                    status.add(ScmTaint.switched,
                        "> commit: configured: '{}', actual: '{}'".format(self.__commit, output))
            elif self.__tag:
                output = self.callGit(workspacePath, 'tag', '--points-at', 'HEAD').splitlines()
                if self.__tag not in output:
                    actual = ("'" + ", ".join(output) + "'") if output else "not on any tag"
                    status.add(ScmTaint.switched,
                        "> tag: configured: '{}', actual: {}".format(self.__tag, actual))

                # Need to check if the tag still exists. Otherwise the "git
                # log" command at the end will trip.
                try:
                    self.callGit(workspacePath, 'rev-parse', 'tags/'+self.__tag)
                    onTag = True
                except BuildError:
                    pass
            elif self.__branch:
                output = self.callGit(workspacePath, 'rev-parse', '--abbrev-ref', 'HEAD')
                if output != self.__branch:
                    status.add(ScmTaint.switched,
                        "> branch: configured: '{}', actual: '{}'".format(self.__branch, output))
                else:
                    output = self.callGit(workspacePath, 'log', '--oneline',
                        'refs/remotes/origin/'+self.__branch+'..HEAD')
                    if output:
                        status.add(ScmTaint.unpushed_main,
                            joinLines("> unpushed commits on {}:".format(self.__branch),
                                indent(output, '   ')))
                    onCorrectBranch = True

            # Check for modifications wrt. checked out commit
            output = self.callGit(workspacePath, 'status', '--porcelain')
            if output:
                status.add(ScmTaint.modified, joinLines("> modified:",
                    indent(output, '   ')))

            # The following shows all unpushed commits reachable by any ref
            # (local branches, stash, detached HEAD, etc).
            # Exclude HEAD if the configured branch is checked out to not
            # double-count them. Does not mark the SCM as dirty. Exclude the
            # configured tag too if it is checked out. Otherwise the tag would
            # count as unpushed if it is not on a remote branch.
            what = ['--all', '--not', '--remotes']
            if onCorrectBranch: what.append('HEAD')
            if onTag: what.append("tags/"+self.__tag)
            output = self.callGit(workspacePath, 'log', '--oneline', '--decorate',
                *what)
            if output:
                status.add(ScmTaint.unpushed_local,
                    joinLines("> unpushed local commits:", indent(output, '   ')))

        except BuildError as e:
            status.add(ScmTaint.error, e.slogan)

        return status

    def getAuditSpec(self):
        return ("git", self.__dir, {})

    def hasLiveBuildId(self):
        return True

    async def predictLiveBuildId(self, step):
        if self.__commit:
            return bytes.fromhex(self.__commit)

        with stepAction(step, "LS-REMOTE", self.__url, (INFO, TRACE)) as a:
            if self.__tag:
                # Annotated tags are objects themselves. We need the commit object!
                refs = ["refs/tags/" + self.__tag + '^{}', "refs/tags/" + self.__tag]
            else:
                refs = ["refs/heads/" + self.__branch]
            cmdLine = ['git', 'ls-remote', self.__url] + refs
            try:
                stdout = await check_output(cmdLine, stderr=subprocess.DEVNULL,
                    universal_newlines=True)
                output = stdout.strip()
            except subprocess.CalledProcessError as e:
                a.fail("exit {}".format(e.returncode), WARNING)
                return None
            except OSError as e:
                a.fail("error ({})".format(e))
                return None

            # have we found anything at all?
            if not output:
                a.fail("unknown", WARNING)
                return None

            # See if we got one of our intended refs. Git is generating lines with
            # the following format:
            #
            #   <sha1>\t<refname>
            #
            # Put the output into a dict with the refname as key. Be extra careful
            # and strip out lines not matching this pattern.
            output = {
                commitAndRef[1].strip() : bytes.fromhex(commitAndRef[0].strip())
                for commitAndRef
                in (line.split('\t') for line in output.split('\n'))
                if len(commitAndRef) == 2 }
            for ref in refs:
                if ref in output: return output[ref]

            # uhh, should not happen...
            a.fail("unknown", WARNING)
            return None

    def calcLiveBuildId(self, workspacePath):
        if self.__commit:
            return bytes.fromhex(self.__commit)
        else:
            output = self.callGit(workspacePath, 'rev-parse', 'HEAD').strip()
            return bytes.fromhex(output)

    def getLiveBuildIdSpec(self, workspacePath):
        if self.__commit:
            return "=" + self.__commit
        else:
            return "g" + os.path.join(workspacePath, self.__dir)

    @staticmethod
    def processLiveBuildIdSpec(dir):
        try:
            return subprocess.check_output(["git", "rev-parse", "HEAD"],
                cwd=dir, universal_newlines=True).strip()
        except subprocess.CalledProcessError as e:
            raise BuildError("Git audit failed: " + str(e))
        except OSError as e:
            raise BuildError("Error calling git: " + str(e))

class GitAudit(ScmAudit):

    SCHEMA = schema.Schema({
        'type' : 'git',
        'dir' : str,
        'remotes' : { schema.Optional(str) : str },
        'commit' : str,
        'description' : str,
        'dirty' : bool
    })

    async def _scanDir(self, workspace, dir, extra):
        self.__dir = dir
        dir = os.path.join(workspace, dir)
        try:
            remotes = (await check_output(["git", "remote", "-v"],
                cwd=dir, universal_newlines=True)).split("\n")
            remotes = (r[:-8].split("\t") for r in remotes if r.endswith("(fetch)"))
            self.__remotes = { remote:url for (remote,url) in remotes }

            self.__commit = (await check_output(["git", "rev-parse", "HEAD"],
                cwd=dir, universal_newlines=True)).strip()
            self.__description = (await check_output(
                ["git", "describe", "--always", "--dirty=-dirty"],
                cwd=dir, universal_newlines=True)).strip()
            self.__dirty = self.__description.endswith("-dirty")
        except subprocess.CalledProcessError as e:
            raise BuildError("Git audit failed: " + str(e))
        except OSError as e:
            raise BuildError("Error calling git: " + str(e))

    def _load(self, data):
        self.__dir = data["dir"]
        self.__remotes = data["remotes"]
        self.__commit = data["commit"]
        self.__description = data["description"]
        self.__dirty = data["dirty"]

    def dump(self):
        return {
            "type" : "git",
            "dir" : self.__dir,
            "remotes" : self.__remotes,
            "commit" : self.__commit,
            "description" : self.__description,
            "dirty" : self.__dirty,
        }

    def getStatusLine(self):
        return self.__description
