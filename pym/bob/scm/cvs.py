# Bob build tool
# Copyright (C) 2016  Stefan Reuther
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ..tty import colorize
from .scm import Scm
import re
import schema
import os
import subprocess

class CvsScm(Scm):

    SCHEMA = schema.Schema({
        'scm' : 'cvs',
        'cvsroot' : str,
        'module' : str,
        schema.Optional('dir') : str,
        schema.Optional('if') : str,
        schema.Optional('rev') : str
    })

    # Checkout using CVS
    # - mandatory parameters: cvsroot, module
    # - optional parameters: rev, dir (dir is required if there are multiple checkouts)
    def __init__(self, spec, overrides=[]):
        super().__init__(spec, overrides)
        self.__cvsroot = spec["cvsroot"]
        self.__module = spec["module"]
        self.__rev = spec.get("rev")
        self.__dir = spec.get("dir", ".")

    def getProperties(self):
        ret = super().getProperties()
        ret.update({
            'scm' : 'cvs',
            'cvsroot' : self.__cvsroot,
            'module' : self.__module,
            'rev' : self.__rev,
            'dir' : self.__dir
        })
        return ret

    def asScript(self):
        # If given a ":ssh:" cvsroot, translate that to CVS_RSH using ssh, and ":ext:"
        # (some versions of CVS do that internally)
        m = re.match('^:ssh:(.*)', self.__cvsroot)
        if m:
            prefix="CVS_RSH=ssh "
            rootarg=":ext:" + m.group(1)
        else:
            prefix=""
            rootarg=self.__cvsroot
        revarg = "-r {rev}".format(rev=self.__rev) if self.__rev != None else "-A"
        header = super().asScript()

        # Workaround: CVS 1.12.13 refuses to checkout with '-d .' when using remote access
        #   cvs checkout: existing repository /home/stefan/cvsroot does not match /home/stefan/cvsroot/cxxtest
        #   cvs checkout: ignoring module cxxtest
        # Thus, we have to trick it with a symlink.
        # Workaround 2: 'cvs co' does not have a '-P' option like 'cvs up' has.
        # This option removes empty (=deleted) directories.
        # We therefore use a 'cvs up' after the initial 'cvs co', to get the same behaviour for the initial and subsequent builds.
        if re.match('^:ext:', rootarg) and self.__dir == '.':
            return """
{header}
# Checkout or update
if ! [ -d CVS ]; then
   ln -s . __tmp$$
   {prefix}cvs -qz3 -d '{rootarg}' co {revarg} -d __tmp$$ '{module}'
   rm __tmp$$
fi
{prefix}cvs -qz3 -d '{rootarg}' up -dP {revarg} .
""".format(header=header, prefix=prefix, rootarg=rootarg, revarg=revarg, module=self.__module)
        else:
            return """
{header}
# Checkout or update
if ! [ -d {dir}/CVS ]; then
   {prefix}cvs -qz3 -d '{rootarg}' co {revarg} -d {dir} '{module}'
fi
{prefix}cvs -qz3 -d '{rootarg}' up -dP {revarg} {dir}
""".format(header=header, prefix=prefix, rootarg=rootarg, revarg=revarg, module=self.__module, dir=self.__dir)

    def asDigestScript(self):
        # Describe what we do: just all the parameters concatenated.
        revarg = "-r {rev}".format(rev=self.__rev) if self.__rev != None else "-A"
        return "{cvsroot} {revarg} {module} {dir}".format(cvsroot=self.__cvsroot,
                                                          revarg=revarg,
                                                          module=self.__module,
                                                          dir=self.__dir)

    def getDirectory(self):
        return self.__dir

    def isDeterministic(self):
        # We cannot know whether this step is deterministic because we
        # don't know whether the given revision (if any) refers to a
        # tag or branch.
        return False

    # CvsScm status.
    #
    # Return values:
    # - empty: workspace does not exist
    # - error: workspace does not contain CVS checkout, or other bad things happen
    # - clean: no local and no remote changes
    # - dirty: local or remote changes (cannot easily distinguish between both with CVS)
    def status(self, workspacePath):
        # Check directories
        workDir = os.path.join(workspacePath, self.__dir)
        cvsDir = os.path.join(workDir, 'CVS')
        if not os.path.exists(workDir):
            return 'empty','',''
        if not os.path.exists(cvsDir):
            return 'error','',''

        # Prepare an environment
        environment = os.environ.copy()
        m = re.match('^:ssh:(.*)', self.__cvsroot)
        if m:
            expectedRoot = ":ext:" + m.group(1)
            environment['CVS_RSH'] = 'ssh'
        else:
            expectedRoot = self.__cvsroot

        # Validate root and module
        status = 'clean'
        shortStatus = ''
        longStatus = ''
        def setStatus(shortMsg, longMsg, dirty=True):
            nonlocal status, shortStatus, longStatus
            if (shortMsg not in shortStatus):
                shortStatus += shortMsg
            longStatus += longMsg
            if (dirty):
                status = 'dirty'

        actualRoot = CvsScm._loadFile(os.path.join(cvsDir, 'Root'))
        actualModule = CvsScm._loadFile(os.path.join(cvsDir, 'Repository'))
        if actualRoot != expectedRoot:
            # Root mismatch counts as switch.
            setStatus("S", colorize("> Root does not match!\n     recipe:\t{}\n     actual:\t{}\n".format(self.__cvsroot, expectedRoot), "33"))
        elif actualModule != self.__module:
            # Module mismatch counts as switch.
            setStatus("S", colorize("> Module does not match!\n     recipe:\t{}\n     actual:\t{}\n".format(self.__module, actualModule), "33"))
        else:
            # Repository matches.
            # There is no (easy) local-only way to determine just local changes AND match it against the requested revision.
            # We therefore just call 'cvs update' in "don't modify" mode and look what it would change.
            # This will contain switched files, local and remote modifications.
            try:
                commandLine = ['cvs', '-Rnq', 'update', '-dP']
                if self.__rev is None:
                    commandLine += ['-A']
                else:
                    commandLine += ['-r', self.__rev]
                output = subprocess.check_output(commandLine,
                                                 cwd=workDir,
                                                 universal_newlines=True,
                                                 env=environment,
                                                 stderr=subprocess.DEVNULL,
                                                 stdin=subprocess.DEVNULL)
            except subprocess.CalledProcessError as e:
                print ("cvs error: '{}' '{}'".format(" ".join(cmdLine), e.output))
                return 'error','',''
            except OSError as e:
                print("Error calling cvs:", str(e))
                return 'error','',''

            modified = False
            #   U = updated remotely, clean or missing locally (but can also mean local is on wrong branch)
            #   P = same, but transferring patch instead of file
            #   A = added locally
            #   R = removed locally
            #   M = modified locally, possibly modified remotely
            #   C = modified locally and remotely, conflict
            #   ? = untracked (could be file forgotten to add)
            # We therefore interpret every nonempty output as a modification
            # (ignoring spurious extra output such as "cvs update: Updating...").
            longMsg = ""
            for i in output.split('\n'):
                if (re.match('^[^ ] ', i)):
                    if not modified: longMsg += colorize("> modified:\n", "33")
                    modified = True
                    longMsg += '  ' + i + '\n'
            if modified:
                setStatus("M", longMsg)

        return status, shortStatus, longStatus

    @staticmethod
    def _loadFile(name):
        try:
            with open(name) as f:
                return f.read().rstrip('\r\n')
        except OSError:
            return ''
