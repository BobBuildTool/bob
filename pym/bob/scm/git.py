# Bob build tool
# Copyright (C) 2016  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ..errors import ParseError, BuildError
from ..stringparser import isTrue, IfExpression
from ..tty import WarnOnce, stepAction, INFO, TRACE, WARNING
from ..utils import check_output, joinLines, run, removeUserFromUrl
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

def normPath(p):
    return os.path.normcase(os.path.normpath(p))

def dirIsEmpty(p):
    if not os.path.isdir(p):
        return False
    return os.listdir(p) == []

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
        schema.Optional('singleBranch') : bool,
        schema.Optional('shallow') : schema.Or(int, str),
        schema.Optional('submodules') : schema.Or(bool, [str]),
        schema.Optional('recurseSubmodules') : bool,
        schema.Optional('shallowSubmodules') : bool,
    })
    REMOTE_PREFIX = "remote-"

    def __init__(self, spec, overrides=[], secureSSL=None, stripUser=None):
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
        self.__singleBranch = spec.get('singleBranch')
        self.__shallow = spec.get('shallow')
        self.__submodules = spec.get('submodules', False)
        self.__recurseSubmodules = spec.get('recurseSubmodules', False)
        self.__shallowSubmodules = spec.get('shallowSubmodules', True)
        self.__stripUser = stripUser

    def getProperties(self, isJenkins):
        properties = super().getProperties(isJenkins)
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
            'singleBranch' : self.__singleBranch,
            'shallow' : self.__shallow,
            'submodules' : self.__submodules,
            'recurseSubmodules' : self.__recurseSubmodules,
            'shallowSubmodules' : self.__shallowSubmodules,
        })
        for key, val in self.__remotes.items():
            properties.update({GitScm.REMOTE_PREFIX+key : val})
        return properties

    async def invoke(self, invoker, switch=False):
        # make sure the git directory exists
        if not os.path.isdir(invoker.joinPath(self.__dir, ".git")):
            await invoker.checkCommand(["git", "init", self.__dir])

        # Shallow implies singleBranch
        if self.__singleBranch is None:
            singleBranch = self.__shallow is not None
        else:
            singleBranch = self.__singleBranch
        singleBranch = singleBranch and (self.__branch is not None)

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
            addCmd = ["git", "remote", "add", remote, url]
            if singleBranch: addCmd += ["-t", self.__branch]
            await invoker.checkCommand(addCmd, cwd=self.__dir)

        # relax security if requested
        if not self.__sslVerify:
            await invoker.checkCommand(["git", "config", "http.sslVerify", "false"], cwd=self.__dir)

        # Calculate refspec that is used internally. For the user a regular
        # refspec is kept in the git config.

        # Base fetch command with shallow support
        fetchCmd = ["git", "-c", "submodule.recurse=0", "fetch", "-p"]
        if isinstance(self.__shallow, int):
            fetchCmd.append("--depth={}".format(self.__shallow))
        elif isinstance(self.__shallow, str):
            fetchCmd.append("--shallow-since={}".format(self.__shallow))
        fetchCmd.append("origin")

        # Calculate appropriate refspec (all/singleBranch/tag)
        if singleBranch:
            fetchCmd += ["+refs/heads/{0}:refs/remotes/origin/{0}".format(self.__branch)]
        else:
            fetchCmd += ["+refs/heads/*:refs/remotes/origin/*"]
        if self.__tag:
            fetchCmd.append("refs/tags/{0}:refs/tags/{0}".format(self.__tag))

        # do the checkout
        if self.__tag or self.__commit:
            await self.__checkoutTag(invoker, fetchCmd, switch)
        else:
            await self.__checkoutBranch(invoker, fetchCmd, switch)

    async def __checkoutTag(self, invoker, fetchCmd, switch):
        # checkout only if HEAD is invalid
        head = await invoker.callCommand(["git", "rev-parse", "--verify", "-q", "HEAD"],
            stdout=False, cwd=self.__dir)
        if head or switch:
            await invoker.checkCommand(fetchCmd, cwd=self.__dir)
            await invoker.checkCommand(["git", "checkout", "-q", "--no-recurse-submodules",
                self.__commit if self.__commit else "tags/"+self.__tag], cwd=self.__dir)
            # FIXME: will not be called again if interrupted!
            await self.__checkoutSubmodules(invoker)

    async def __checkoutBranch(self, invoker, fetchCmd, switch):
        await invoker.checkCommand(fetchCmd, cwd=self.__dir)
        if await invoker.callCommand(["git", "rev-parse", "--verify", "-q", "HEAD"],
                       stdout=False, cwd=self.__dir):
            # checkout only if HEAD is invalid
            await invoker.checkCommand(["git", "checkout", "--no-recurse-submodules", "-b", self.__branch,
                "remotes/origin/"+self.__branch], cwd=self.__dir)
            await self.__checkoutSubmodules(invoker)
        elif switch:
            # We're switching the ref. There we will actively change the branch which
            # is normally forbidden.
            assert not self.__submodules
            if await invoker.callCommand(["git", "show-ref", "-q", "--verify",
                                          "refs/heads/" + self.__branch]):
                # Branch does not exist. Create and checkout.
                await invoker.checkCommand(["git", "checkout", "--no-recurse-submodules",
                    "-b", self.__branch, "remotes/origin/"+self.__branch], cwd=self.__dir)
            else:
                # Branch exists already. Checkout and fast forward...
                await invoker.checkCommand(["git", "checkout", "--no-recurse-submodules",
                    self.__branch], cwd=self.__dir)
                await invoker.checkCommand(["git", "-c", "submodule.recurse=0", "merge",
                    "--ff-only", "refs/remotes/origin/"+self.__branch], cwd=self.__dir)
        elif (await invoker.checkOutputCommand(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=self.__dir)) == self.__branch:
            # pull only if on original branch
            preUpdate = await self.__updateSubmodulesPre(invoker)
            await invoker.checkCommand(["git", "-c", "submodule.recurse=0", "merge", "--ff-only", "refs/remotes/origin/"+self.__branch], cwd=self.__dir)
            await self.__updateSubmodulesPost(invoker, preUpdate)
        else:
            invoker.warn("Not updating", self.__dir, "because branch was changed manually...")

    async def __checkoutSubmodules(self, invoker):
        if not self.__submodules: return

        args = ["git", "submodule", "update", "--init"]
        if self.__shallowSubmodules:
            args += ["--depth", "1"]
        if self.__recurseSubmodules:
            args += ["--recursive"]
        if isinstance(self.__submodules, list):
            args.append("--")
            args.extend(self.__submodules)
        await invoker.checkCommand(args, cwd=self.__dir)

    async def __updateSubmodulesPre(self, invoker, base = "."):
        """Query the status of the currently checked out submodules.

        Returns a map with the paths of all checked out submodules as keys.
        The value will be True if the submodule looks untouched by the user and
        is deemed to be updateable. If the value is False the submodule is
        different from the expected vanilla checkout state. The list may only
        be a sub-set of all known submodules.
        """

        if not self.__submodules:
            return {}

        # List all active and checked out submodules. This way we know the
        # state of all submodules and compare them later to the expected state.
        args = [ "git", "-C", base, "submodule", "-q", "foreach",
                 "printf '%s\\t%s\\n' \"$sm_path\" \"$(git rev-parse HEAD)\""]
        checkedOut = await invoker.checkOutputCommand(args, cwd=self.__dir)
        checkedOut = {
            path : commit for path, commit
                in ( line.split("\t") for line in checkedOut.split("\n") if line )
        }
        if not checkedOut: return {}

        # List commits from git tree of all paths for checked out submodules.
        # This is what should be checked out.
        args = [ "git", "-C", base, "ls-tree", "-z", "HEAD"] + sorted(checkedOut.keys())
        allPaths = await invoker.checkOutputCommand(args, cwd=self.__dir)
        allPaths = {
            normPath(path) : attribs.split(' ')[2]
                for attribs, path
                in ( p.split('\t') for p in allPaths.split('\0') if p )
                if attribs.split(' ')[1] == "commit"
        }

        # Calculate which paths are in the right state. They must match the
        # commit and must be in detached HEAD state.
        ret = {}
        for path, commit in checkedOut.items():
            path = normPath(path)
            if allPaths.get(path) != commit:
                ret[path] = False
                continue

            code = await invoker.callCommand(["git", "symbolic-ref", "-q", "HEAD"],
                cwd=os.path.join(self.__dir, base, path))
            if code == 0:
                ret[path] = False
                continue

            ret[path] = True

        return ret

    async def __updateSubmodulesPost(self, invoker, oldState, base = "."):
        """Update all submodules that are safe.

        Will update all submodules that are either new or have not been touched
        by the user. This will be done recursively if that is enabled.
        """
        if not self.__submodules:
            return {}
        if not os.path.exists(invoker.joinPath(self.__dir, base, ".gitmodules")):
            return {}

        # Sync remote URLs into our config in case they were changed
        args = ["git", "-C", base, "submodule", "sync"]
        await invoker.checkCommand(args, cwd=self.__dir)

        # List all paths as per .gitmodules. This gives us the list of all
        # known submodules. Optionally restrict to user specified subset.
        args = [ "git", "-C", base, "config", "-f", ".gitmodules", "-z", "--get-regexp",
                 "path" ]
        allPaths = await invoker.checkOutputCommand(args, cwd=self.__dir)
        allPaths = [ p.split("\n")[1] for p in allPaths.split("\0") if p ]
        if isinstance(self.__submodules, list):
            subset = set(normPath(p) for p in self.__submodules)
            allPaths = [ p for p in allPaths if normPath(p) in subset ]

        # Update only new or unmodified paths
        updatePaths = [ p for p in allPaths if oldState.get(normPath(p), True) ]
        for p in sorted(set(allPaths) - set(updatePaths)):
            invoker.warn("Not updating submodule", os.path.join(self.__dir, base, p), "because its HEAD has been switched...")
        if not updatePaths:
            return

        # If we recurse into sub-submodules get their potential state up-front
        if self.__recurseSubmodules:
            # Explicit loop because of Python 3.5: "'await' expressions in
            # comprehensions are not supported".
            subMods = {}
            for p in updatePaths:
                subMods[p] = await self.__updateSubmodulesPre(invoker,
                    os.path.join(base, p))

        # Do the update of safe submodules
        args = ["git", "-C", base, "submodule", "update", "--init"]
        if self.__shallowSubmodules:
            args += ["--depth", "1"]
        args.append("--")
        args += updatePaths
        await invoker.checkCommand(args, cwd=self.__dir)

        # Update sub-submodules if requested
        if self.__recurseSubmodules:
            for p in updatePaths:
                await self.__updateSubmodulesPost(invoker, subMods[p],
                                                  os.path.join(base, p))

    def canSwitch(self, oldSpec):
        diff = self._diffSpec(oldSpec)

        # Filter irrelevant properties
        diff -= {"sslVerify", 'singleBranch', 'shallow', 'shallowSubmodules'}
        diff = set(prop for prop in diff if not prop.startswith("remote-"))

        # Enabling "submodules" and/or "recurseSubmodules" is ok. The
        # additional content will be checked out in invoke().
        if not oldSpec.get("submodules", False) and self.__submodules:
            diff.discard("submodules")
        if not oldSpec.get("recursiveSubmodules", False) and self.__recurseSubmodules:
            diff.discard("recursiveSubmodules")

        # Without submodules the recursiveSubmodules property is irrelevant
        if not self.__submodules:
            diff.discard("recursiveSubmodules")

        # For the rest we can try a inline switch. Git does not handle
        # vanishing submodules well and neither do we. So if submodules are
        # enabled then we do not do an in-place update.
        if not diff:
            return True
        if not diff.issubset({"branch", "tag", "commit", "rev", "url"}):
            return False
        if self.__submodules:
            return False
        return True

    async def switch(self, invoker, oldSpec):
        # Try to checkout new state in old workspace. If something fails the
        # old attic logic will take over.
        await self.invoke(invoker, True)
        return True

    def asDigestScript(self):
        """Return forward compatible stable string describing this git module.

        The format is "url rev-spec dir" where rev-spec depends on the given reference.
        """
        if self.__stripUser:
            filt = removeUserFromUrl
        else:
            filt = lambda x: x

        if self.__commit:
            ret = self.__commit + " " + self.__dir
        elif self.__tag:
            ret = filt(self.__url) + " refs/tags/" + self.__tag + " " + self.__dir
        else:
            ret = filt(self.__url) + " refs/heads/" + self.__branch + " " + self.__dir

        if self.__submodules:
            ret += " submodules"
            if isinstance(self.__submodules, list):
                ret += "[{}]".format(",".join(self.__submodules))
            if self.__recurseSubmodules:
                ret += " recursive"

        return ret

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
        if isinstance(self.__shallow, int):
            shallow = str(self.__shallow)
        else:
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
                    raise BuildError("Invalid 'git.timeout' option: " + str(timeout))
                if timeout > 0:
                    ElementTree.SubElement(co, "timeout").text = str(timeout)

        if self.__submodules:
            assert isinstance(self.__submodules, bool)
            sub = ElementTree.SubElement(extensions,
                    "hudson.plugins.git.extensions.impl.SubmoduleOption")
            if self.__recurseSubmodules:
                ElementTree.SubElement(sub, "recursiveSubmodules").text = "true"
            if self.__shallowSubmodules:
                ElementTree.SubElement(sub, "shallow").text = "true"
                ElementTree.SubElement(sub, "depth").text = "1"
            if timeout is not None:
                ElementTree.SubElement(sub, "timeout").text = str(timeout)

        if isTrue(options.get("scm.ignore-hooks", "0")):
            ElementTree.SubElement(extensions,
                "hudson.plugins.git.extensions.impl.IgnoreNotifyCommit")

        return scm

    def getDirectory(self):
        return self.__dir

    def isDeterministic(self):
        return bool(self.__tag) or bool(self.__commit)

    def hasJenkinsPlugin(self):
        # Cloning a subset of submodules is not supported by the Jenkins
        # git-plugin. Fall back to our implementation in this case.
        return not isinstance(self.__submodules, list)

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
            output = self.callGit(workspacePath, 'status', '--porcelain', '--ignore-submodules=all')
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

            # Dive into submodules
            self.__statusSubmodule(workspacePath, status, self.__submodules)

        except BuildError as e:
            status.add(ScmTaint.error, e.slogan)

        return status

    def __statusSubmodule(self, workspacePath, status, shouldExist, base = "."):
        """Get the status of submodules and possibly sub-submodules.

        The regular "git status" command is not sufficient for our case. In
        case the submodule is not initialized "git status" will completely
        ignore it. Using "git submodule status" would help but it's output is
        not ment to be parsed by tools.

        So we first get the list of all possible submodules with their tracked
        commit. Then the actual commit is compared and any further
        modifications and unpuched commits are checked.
        """
        if not os.path.exists(os.path.join(workspacePath, base, ".gitmodules")):
            return

        # List all paths as per .gitmodules. This gives us the list of all
        # known submodules.
        allPaths = self.callGit(workspacePath, "-C", base, "config", "-f",
            ".gitmodules", "-z", "--get-regexp", "path")
        allPaths = [ p.split("\n")[1] for p in allPaths.split("\0") if p ]
        if not allPaths:
            return

        # Fetch the respecive commits as per git ls-tree
        allPaths = self.callGit(workspacePath,  "-C", base, "ls-tree", "-z",
            "HEAD", *allPaths)
        allPaths = {
            path : attribs.split(' ')[2]
                for attribs, path
                in ( p.split('\t') for p in allPaths.split('\0') if p )
                if attribs.split(' ')[1] == "commit"
        }

        # Normalize subset of submodules
        if isinstance(shouldExist, list):
            shouldExist = set(normPath(p) for p in shouldExist)
        elif shouldExist:
            shouldExist = set(normPath(p) for p in allPaths.keys())
        else:
            shouldExist = set()

        # Check each submodule for their commit, modifications and unpushed
        # stuff. Unconditionally recurse to even see if something is there even
        # tough it shouldn't.
        for path, commit in sorted(allPaths.items()):
            subPath = os.path.join(base, path)
            subShouldExist = normPath(path) in shouldExist
            if not os.path.exists(os.path.join(workspacePath, subPath, ".git")):
                if subShouldExist:
                    status.add(ScmTaint.modified, "> submodule not checked out: " + subPath)
                elif not dirIsEmpty(os.path.join(workspacePath, subPath)):
                    status.add(ScmTaint.modified, "> ignored submodule not empty: " + subPath)
                continue
            elif not subShouldExist:
                status.add(ScmTaint.modified, "> submodule checked out: " + subPath)

            realCommit = self.callGit(workspacePath, "-C", subPath, "rev-parse", "HEAD")
            if commit != realCommit:
                status.add(ScmTaint.switched,
                    "> submodule '{}' switched commit: configured: '{}', actual: '{}'"
                        .format(subPath, commit, realCommit))

            output = self.callGit(workspacePath, "-C", subPath, 'status',
                '--porcelain', '--ignore-submodules=all')
            if output:
                status.add(ScmTaint.modified, joinLines(
                    "> submodule '{}' modified:".format(subPath),
                    indent(output, '   ')))

            output = self.callGit(workspacePath, "-C", subPath, 'log',
                '--oneline', '--decorate', '--all', '--not', '--remotes')
            if output:
                status.add(ScmTaint.unpushed_local, joinLines(
                    "> submodule '{}' unpushed local commits:".format(subPath),
                    indent(output, '   ')))

            self.__statusSubmodule(workspacePath, status,
                self.__recurseSubmodules, subPath)


    def getAuditSpec(self):
        extra = {}
        if self.__submodules:
            extra['submodules'] = self.__submodules
            if self.__recurseSubmodules:
                extra['recurseSubmodules'] = True
        return ("git", self.__dir, extra)

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
        'dirty' : bool,
        schema.Optional('submodules') : schema.Or(bool, [str]),
        schema.Optional('recurseSubmodules') : bool,
    })

    async def _scanDir(self, workspace, dir, extra):
        self.__dir = dir
        self.__submodules = extra.get('submodules', False)
        self.__recurseSubmodules = extra.get('recurseSubmodules', False)
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
            subDirty = await self.__scanSubmodules(dir, self.__submodules)
            self.__dirty = subDirty or self.__description.endswith("-dirty")
        except subprocess.CalledProcessError as e:
            raise BuildError("Git audit failed: " + str(e))
        except OSError as e:
            raise BuildError("Error calling git: " + str(e))

    async def __scanSubmodules(self, dir, shouldExist, base = "."):
        if not os.path.exists(os.path.join(dir, base, ".gitmodules")):
            return False

        # List all paths as per .gitmodules. This gives us the list of all
        # known submodules.
        allPaths = await check_output(["git", "-C", base, "config", "-f",
            ".gitmodules", "-z", "--get-regexp", "path"], cwd=dir,
            universal_newlines=True)
        allPaths = [ p.split("\n")[1] for p in allPaths.split("\0") if p ]
        if not allPaths:
            return False

        # Fetch the respecive commits as per git ls-tree
        allPaths = await check_output(["git", "-C", base, "ls-tree", "-z",
            "HEAD"] + allPaths, cwd=dir, universal_newlines=True)
        allPaths = {
            path : attribs.split(' ')[2]
                for attribs, path
                in ( p.split('\t') for p in allPaths.split('\0') if p )
                if attribs.split(' ')[1] == "commit"
        }

        # Normalize subset of submodules
        if isinstance(shouldExist, list):
            shouldExist = set(normPath(p) for p in shouldExist)
        elif shouldExist:
            shouldExist = set(normPath(p) for p in allPaths.keys())
        else:
            shouldExist = set()

        # Check each submodule for their commit and modifications.
        # Unconditionally recurse to even see if something is there even tough
        # it shouldn't. Bail out on first modification.
        for path, commit in sorted(allPaths.items()):
            subPath = os.path.join(base, path)
            subShouldExist = normPath(path) in shouldExist
            if not os.path.exists(os.path.join(dir, subPath, ".git")):
                if subShouldExist:
                    return True # submodule is missing
                elif not dirIsEmpty(os.path.join(dir, subPath)):
                    return True # something in submodule which should not be there
                else:
                    continue
            elif not subShouldExist:
                # submodule checked out even though it shouldn't
                return True

            realCommit = (await check_output(["git", "-C", subPath, "rev-parse", "HEAD"],
                cwd=dir, universal_newlines=True)).strip()
            if commit != realCommit:
                return True # different commit checked out

            proc = await run(["git", "-C", subPath, "diff-index", "--quiet",
                "HEAD", "--"], cwd=dir)
            if proc.returncode != 0:
                return True # dirty

            if await self.__scanSubmodules(dir, self.__recurseSubmodules, subPath):
                return True # sub-submodule modified

        return False

    def _load(self, data):
        self.__dir = data["dir"]
        self.__remotes = data["remotes"]
        self.__commit = data["commit"]
        self.__description = data["description"]
        self.__dirty = data["dirty"]
        self.__submodules = data.get("submodules", False)
        self.__recurseSubmodules = data.get("recurseSubmodules", False)

    def dump(self):
        ret = {
            "type" : "git",
            "dir" : self.__dir,
            "remotes" : self.__remotes,
            "commit" : self.__commit,
            "description" : self.__description,
            "dirty" : self.__dirty,
        }
        if self.__submodules:
            ret["submodules"] = self.__submodules
            if self.__recurseSubmodules:
                ret["recurseSubmodules"] = True
        return ret

    def getStatusLine(self):
        return self.__description
