# Bob Build Tool
# Copyright (C) 2020  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from .errors import BuildError
from .input import CheckoutAssert
from .scm import getScm
from .stringparser import Env
from .tty import Unbuffered
from .utils import removePath, emptyDirectory, isWindows
from enum import Enum
from shlex import quote
import asyncio
import concurrent.futures
import datetime
import io
import locale
import os
import re
import shutil
import subprocess
import sys
import tempfile

__all__ = ['InvocationMode', 'Invoker']


class InvocationError(Exception):
    def __init__(self, returncode, what):
        self.returncode = returncode
        self.what = what

class CmdFailedError(InvocationError):
    def __init__(self, cmd, returncode):
        super().__init__(returncode, "Command '{}' returned exit status {}".format(cmd, returncode))
        self.cmd = cmd

class FinishedProcess:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

class BlackHole:
    def write(self, data):
        pass
    def close(self):
        pass

DEVNULL = BlackHole()

class LogWriteProtocol(asyncio.SubprocessProtocol):
    def __init__(self, exitFuture, logFile, stdOut, stdErr):
        self.__exit = exitFuture
        self.__logFile = logFile
        self.__stdOut = stdOut
        self.__stdErr = stdErr

    def connection_made(self, transport):
        """Process is running and pipes have been connected"""
        self.__transport = transport

    def connection_lost(self, exc):
        """Process exited and all pipes have been disconnected"""
        self.__exit.set_result(self.__transport.get_returncode())
        self.__transport = None

    def pipe_data_received(self, fd, data):
        self.__logFile.write(data)
        if fd == 1:
            for f in self.__stdOut: f.write(data)
        elif fd == 2:
            for f in self.__stdErr: f.write(data)


class InvocationMode(Enum):
    CALL = 'call'
    SHELL = 'shell'
    UPDATE = 'update'

class Invoker:
    def __init__(self, spec, preserveEnv, noLogFiles, showStdOut, showStdErr,
                 trace, redirect, executor=None):
        self.__spec = spec
        self.__cwd = spec.workspaceWorkspacePath
        self.__preserveEnv = preserveEnv
        if preserveEnv:
            self.__env = os.environ.copy()
        else:
            self.__env = { k:v for (k,v) in os.environ.items()
                                         if k in spec.envWhiteList }
        self.__logFileName = None if noLogFiles else spec.logFile
        self.__logFile = DEVNULL
        self.__makeFds = []
        self.__makeJobs = None
        self.__trace = trace
        self.__sandboxHelperPath = None
        self.__stdioBuffer = io.BytesIO() if redirect else None
        self.__warnedDuplicates = { '' }
        self.__executor = executor

        # Redirection is a bit complicated. We have to consider two levels: the
        # optional log file and the console.
        #
        #  * If there is a logfile then _all_ output is captured there.
        #  * What is printed is determined by the showStdOut/showStdErr knobs
        #  * Either the prints are sent to stdout/err or to the __stdioBuffer
        #    if all output is redirected/captured
        #  * If no log file and no redirection is used then the real tty should
        #    be used to let the called applications use all tty features (e.g.
        #    colors).
        if redirect:
            # Everything is redirected into a local buffer. Nothing is printed
            # on the console.
            self.__stdin = subprocess.DEVNULL
            if noLogFiles:
                self.__stdout = subprocess.PIPE if showStdOut else subprocess.DEVNULL
                self.__stderr = subprocess.PIPE if showStdErr else subprocess.DEVNULL
            else:
                self.__stdout = self.__stderr = subprocess.PIPE
            self.__stdoutStream = self.__stdioBuffer if showStdOut else DEVNULL
            self.__stderrStream = self.__stdioBuffer if showStdErr else DEVNULL
        else:
            # Depending on the verbosity some output is visible on the tty.
            self.__stdin = None
            if noLogFiles:
                self.__stdout = None if showStdOut else subprocess.DEVNULL
                self.__stderr = None if showStdErr else subprocess.DEVNULL
            else:
                self.__stdout = self.__stderr = subprocess.PIPE
            self.__stdoutStream = Unbuffered(sys.stdout.buffer) if showStdOut else DEVNULL
            self.__stderrStream = Unbuffered(sys.stderr.buffer) if showStdErr else DEVNULL

    def __openLog(self, header=None):
        # Create log file
        if self.__logFileName:
            self.__logFile = open(self.__logFileName, "ab", buffering=0)
            self.__logFile.write("### START: {}{}\n"
                .format(datetime.datetime.now().ctime(),
                        (" (" + header + ")") if header else "")
                .encode(locale.getpreferredencoding()))
        else:
            self.__logFile = DEVNULL

    def __closeLog(self, lastExit):
        try:
            self.__logFile.write("### END({}): {}\n"
                .format(lastExit, datetime.datetime.now().ctime())
                .encode(locale.getpreferredencoding()))
            self.__logFile.close()
            self.__logFile = DEVNULL
        except OSError as e:
            self.error("Cannot close log file:", str(e))

    def __getSandboxHelperPath(self):
        if self.__sandboxHelperPath is None:
            # If Bob is run from the source directly we have to make sure that
            # the sandbox helper is up-to-date and use it from there. Otherwise
            # we assume that Bob is properly installed and that
            # bob-namespace-sandbox is in $PATH.
            try:
                from .develop.make import makeSandboxHelper
                sandboxHelper = makeSandboxHelper()
            except ImportError:
                # Determine absolute path here. We set $PATH when running in
                # the sandbox so we might not find it anymore.
                sandboxHelper = shutil.which("bob-namespace-sandbox")
                if sandboxHelper is None:
                    raise BuildError("Could not find bob-namespace-sandbox in $PATH! Please check your Bob installation.")

            self.__sandboxHelperPath = sandboxHelper

        return self.__sandboxHelperPath

    async def __runCommand(self, args, cwd, stdout=None, stderr=None,
                           check=False, env=None, universal_newlines=True,
                           errors='replace', specEnv=True, **kwargs):
        cmd = " ".join(quote(a) for a in args)
        self.trace(cmd)

        _env = self.__env.copy()
        if specEnv: _env.update(self.__spec.env)
        if env is not None: _env.update(env)
        env = _env

        if stdout == True:
            # If stdout should be captured we use a dedicated buffer. This
            # buffer is then returned to the caller at child exit.
            stdoutRedir = subprocess.PIPE
            stdoutStreams = [io.BytesIO(), self.__stdoutStream]
        elif stdout == False:
            stdoutRedir = subprocess.DEVNULL
            stdoutStreams = []
        else:
            stdoutRedir = self.__stdout
            stdoutStreams = [self.__stdoutStream]

        if stderr == True:
            # If stderr should be captured we use a dedicated buffer. This
            # buffer is then returned to the caller at child exit.
            stderrRedir = subprocess.PIPE
            stderrStreams = [io.BytesIO(), self.__stderrStream]
        elif stdout == False:
            stderrRedir = subprocess.DEVNULL
            stderrStreams = []
        else:
            stderrRedir = self.__stderr
            stderrStreams = [self.__stderrStream]

        # Sanity check on Windows that there are no environment variables that
        # differ only in case. The Windows envrionment is used case insensitive
        # by libc getenv() but the kernel passes the variables as-is. We warn
        # only about variables that are defined in the recipe. It's not our
        # business if the inherited OS environment already has duplicates.
        if isWindows():
            duplicates = set()
            definedEnvVars = set(self.__spec.env.keys())
            allEnvVars = set(env.keys()) | definedEnvVars
            for i in definedEnvVars:
                matchedVars = [ v for v in allEnvVars if v.upper() == i.upper() ]
                if len(matchedVars) > 1:
                    duplicates.add(" vs. ".join(sorted(matchedVars)))
            duplicates = ", ".join(sorted(duplicates))
            if duplicates not in self.__warnedDuplicates:
                self.warn("Duplicate environment variables:", duplicates+"!",
                    "It is unspecified which variant is used by the invoked processes.")
                self.__warnedDuplicates.add(duplicates)

        loop = asyncio.get_event_loop()
        exitFuture = asyncio.Future()
        try:
            transport, protocol = await loop.subprocess_exec(
                lambda: LogWriteProtocol(exitFuture, self.__logFile,
                                         stdoutStreams, stderrStreams),
                *args,
                stdin=self.__stdin, stdout=stdoutRedir, stderr=stderrRedir,
                env=env, cwd=cwd,
                **kwargs, pass_fds=self.__makeFds)
        except OSError as e:
            self.fail(str(e), returncode=127)

        try:
            ret = await exitFuture
        finally:
            transport.close()
        if check and ret != 0:
            raise CmdFailedError(cmd, ret)

        if stdout == True:
            if universal_newlines:
                stdoutStreams[0].seek(0)
                stdoutBuf = io.TextIOWrapper(stdoutStreams[0], errors=errors).read()
            else:
                stdoutBuf = stdoutStreams[0].getvalue()
        else:
            stdoutBuf = None

        if stderr == True:
            if universal_newlines:
                stderrStreams[0].seek(0)
                stderrBuf = io.TextIOWrapper(stderrStreams[0], errors=errors).read()
            else:
                stderrBuf = stderrStreams[0].getvalue()
        else:
            stderrBuf = None

        return FinishedProcess(ret, stdoutBuf, stderrBuf)

    def __getSandboxCmds(self, tmpDir):
        if sys.platform != "linux":
            self.fail("Sandbox builds are only supported on Linux!")

        cmdArgs = [ self.__getSandboxHelperPath() ]
        #FIXME: if verbosity >= 4: cmdArgs.append('-D')
        cmdArgs.extend(["-S", tmpDir])
        cmdArgs.extend(["-H", "bob"])
        cmdArgs.extend(["-d", "/tmp"])
        sandboxRootFs = os.path.abspath(self.__spec.sandboxRootWorkspace)
        for f in os.listdir(sandboxRootFs):
            cmdArgs.extend(["-M", os.path.join(sandboxRootFs, f), "-m", "/"+f])

        skipOpt = "nojenkins" if self.__spec.isJenkins else "nolocal"
        substEnv = Env(self.__env)
        for (hostPath, sndbxPath, options) in self.__spec.sandboxHostMounts:
            if skipOpt in options: continue
            hostPath = substEnv.substitute(hostPath, hostPath, False)
            if "nofail" in options:
                if not os.path.exists(hostPath): continue
            sndbxPath = substEnv.substitute(sndbxPath, sndbxPath, False)
            cmdArgs.extend(["-M", hostPath])
            if "rw" in options:
                cmdArgs.extend(["-w", sndbxPath])
            elif hostPath != sndbxPath:
                cmdArgs.extend(["-m", sndbxPath])

        return cmdArgs

    async def executeStep(self, mode, clean=False, keepSandbox=False):
        # make permissions predictable
        os.umask(0o022)

        tmpDir = None
        ret = 1
        try:
            self.__openLog()

            # Create temporary directory for the script file (or others as
            # needed by the particular scripting engine). This directory is
            # also used as ephemeral sandbox container.
            tmpDir = tempfile.mkdtemp()

            # prepare workspace
            clean = self.__spec.clean if self.__spec.clean is not None else clean
            if not os.path.isdir(self.__spec.workspaceWorkspacePath):
                os.makedirs(self.__spec.workspaceWorkspacePath, exist_ok=True)
            elif clean and mode != InvocationMode.SHELL:
                emptyDirectory(self.__spec.workspaceWorkspacePath)

            if len(self.__makeFds) == 2:
                makeFlags = self.__spec.env.get("MAKEFLAGS")
                if makeFlags is not None:
                    makeFlags = re.sub(r'-j\s*[0-9]*', '', makeFlags)
                    makeFlags = re.sub(r'--jobserver-auth=[0-9]*,[0-9]*', '', makeFlags)
                else:
                    makeFlags = ""
                self.__spec.env["MAKEFLAGS"] = (makeFlags + " -j" + str(self.__makeJobs)
                    + " --jobserver-auth=" + ",".join([str(fd) for fd in self.__makeFds]))

            # setup script and arguments
            if mode == InvocationMode.SHELL:
                realScriptFile, execScriptFile, callArgs = self.__spec.language.setupShell(
                    self.__spec, tmpDir, self.__preserveEnv)
            elif mode == InvocationMode.CALL:
                realScriptFile, execScriptFile, callArgs = self.__spec.language.setupCall(
                    self.__spec, tmpDir, self.__preserveEnv, self.__trace)
            elif mode == InvocationMode.UPDATE:
                realScriptFile, execScriptFile, callArgs = self.__spec.language.setupUpdate(
                    self.__spec, tmpDir, self.__preserveEnv, self.__trace)
            else:
                assert False, "not reached"

            # Wrap call into sandbox if requested
            if self.__spec.hasSandbox:
                cmdArgs = self.__getSandboxCmds(tmpDir)
                cmdArgs.extend(["-M", os.path.abspath(realScriptFile), "-m"
                    , execScriptFile])

                # Prevent network access
                if not self.__spec.sandboxNetAccess: cmdArgs.append('-n')

                # Create empty env file. Otheriwse bind mount fails.
                if self.__spec.envFile:
                    with open(self.__spec.envFile, "wb"): pass
                    cmdArgs.extend(["-M", os.path.abspath(self.__spec.envFile), "-w", "/bob/env"])

                # Mount workspace writable and all dependencies read-only
                cmdArgs.extend(["-M", os.path.abspath(self.__spec.workspaceWorkspacePath),
                    "-w", self.__spec.workspaceExecPath])
                cmdArgs.extend(["-W", os.path.abspath(self.__spec.workspaceExecPath) ])
                for argWorkspacePath, argExecPath in self.__spec.sandboxDepMounts:
                    cmdArgs.extend(["-M", os.path.abspath(argWorkspacePath),
                        "-m", argExecPath])

                # Command follows. Stop parsing options.
                cmdArgs.append("--")
            else:
                cmdArgs = []
            cmdArgs.extend(callArgs)

            if mode == InvocationMode.SHELL:
                ret = await self.callCommand(cmdArgs, specEnv=False)
            elif mode in (InvocationMode.CALL, InvocationMode.UPDATE):
                for scm in self.__spec.preRunCmds:
                    scm = getScm(scm)
                    if mode == InvocationMode.UPDATE and not scm.isLocal():
                        continue # Skip non-local SCMs on update-only
                    try:
                        await scm.invoke(self)
                    except CmdFailedError as e:
                        self.error(scm.getSource(), "failed")
                        self.error(e.what)
                        raise
                    except Exception:
                        self.error(scm.getSource(), "failed")
                        raise
                await self.checkCommand(cmdArgs, specEnv=False)
                for a in self.__spec.postRunCmds:
                    a = CheckoutAssert(a)
                    try:
                        await a.invoke(self)
                    except Exception:
                        self.error(a.getSource(), "failed")
                        raise
            else:
                assert False, "not reached"

            # everything went well
            ret = 0

        except OSError as e:
            self.error("Something went wrong:", str(e))
            ret = 1
        except InvocationError as e:
            ret = e.returncode
        finally:
            if tmpDir is not None:
                if not self.__spec.hasSandbox or not keepSandbox:
                    try:
                        removePath(tmpDir)
                    except OSError as e:
                        self.error("Error removing sandbox:", str(e))
                elif self.__spec.hasSandbox:
                    self.info("Keeping sandbox image at", tmpDir)
            self.__closeLog(ret)

        return ret

    async def executeFingerprint(self, keepSandbox=False):
        # make permissions predictable
        os.umask(0o022)

        tmpDir = None
        proc = None
        ret = -1
        stdout = stderr = b''
        try:
            self.__openLog("fingerprint")

            # The fingerprint is always exectuted in a temporary directory
            tmpDir = tempfile.mkdtemp(dir=os.getcwd(), prefix=".bob-")

            # Wrap call into sandbox if requested
            env = {}
            if self.__spec.hasSandbox:
                env["BOB_CWD"] = "/bob/fingerprint"
                env["PATH"] = ":".join(self.__spec.sandboxPaths)

                # Setup workspace
                cmdArgs = self.__getSandboxCmds(tmpDir)
                cmdArgs.extend(["-d", "/bob/fingerprint"])
                cmdArgs.extend(["-W", "/bob/fingerprint"])

                # Prevent network access
                cmdArgs.append('-n')

                # Command follows. Stop parsing options.
                cmdArgs.append("--")
            else:
                env["BOB_CWD"] = tmpDir
                cmdArgs = []

            cmdArgs += self.__spec.language.setupFingerprint(self.__spec, env, self.__trace)
            proc = await self.__runCommand(cmdArgs, tmpDir, env=env,
                universal_newlines=False, stdout=True, stderr=True,
                specEnv=False)
            ret = proc.returncode
            stdout = proc.stdout
            stderr = proc.stderr

        except InvocationError as e:
            ret = e.returncode
            raise BuildError(e.what)
        finally:
            if tmpDir is not None:
                if not self.__spec.hasSandbox or not keepSandbox:
                    try:
                        removePath(tmpDir)
                    except OSError as e:
                        self.error("Error removing sandbox:", str(e))
                elif self.__spec.hasSandbox:
                    self.info("Keeping sandbox image at", tmpDir)
            self.__closeLog(ret)

        return (ret, stdout, stderr)

    async def executeScmSwitch(self, scm, oldSpec):
        # make permissions predictable
        os.umask(0o022)

        ret = 1
        try:
            self.__openLog("SCM inline switch")
            try:
                await scm.switch(self, oldSpec)
            except CmdFailedError as e:
                self.error(scm.getSource(), "failed")
                self.error(e.what)
                raise
            except Exception:
                self.error(scm.getSource(), "failed")
                raise

            # everything went well
            ret = 0

        except OSError as e:
            self.error("Something went wrong:", str(e))
            ret = 1
        except InvocationError as e:
            ret = e.returncode
        finally:
            self.__closeLog(ret)

        return ret

    async def runCommand(self, args, cwd=None, **kwargs):
        cwd = os.path.join(self.__cwd, cwd) if cwd else self.__cwd
        ret = await self.__runCommand(args, cwd, **kwargs)
        return ret

    async def callCommand(self, args, cwd=None, retries=0, success=0, **kwargs):
        cwd = os.path.join(self.__cwd, cwd) if cwd else self.__cwd
        while True:
            ret = await self.__runCommand(args, cwd, **kwargs)
            if (ret == success) or (retries == 0):
                break
            await asyncio.sleep(3)
            retries -= 1
        return ret.returncode

    async def checkCommand(self, args, cwd=None, retries=0, **kwargs):
        cwd = os.path.join(self.__cwd, cwd) if cwd else self.__cwd
        while retries != 0:
            ret = await self.__runCommand(args, cwd, **kwargs)
            if ret.returncode == 0:
                return
            await asyncio.sleep(3)
            retries -= 1
        if retries == 0:
            await self.__runCommand(args, cwd, check=True, **kwargs)

    async def checkOutputCommand(self, args, cwd=None, **kwargs):
        cwd = os.path.join(self.__cwd, cwd) if cwd else self.__cwd
        ret = await self.__runCommand(args, cwd, stdout=True, check=True, **kwargs)
        return ret.stdout.rstrip()

    def __print(self, *args, file=DEVNULL, **kwargs):
        buf = io.StringIO()
        print(*args, **kwargs, file=buf)
        binbuf = buf.getvalue().encode(locale.getpreferredencoding())
        file.write(binbuf)
        self.__logFile.write(binbuf)
        return buf.getvalue()

    def trace(self, *args, **kwargs):
        if self.__trace:
            return self.__print("+", *args, **kwargs, file=self.__stderrStream)

    def info(self, *args, **kwargs):
        return self.__print("Info:", *args, **kwargs, file=self.__stderrStream)

    def warn(self, *args, **kwargs):
        return self.__print("Warning:", *args, **kwargs, file=self.__stderrStream)

    def error(self, *args, **kwargs):
        return self.__print("Error:", *args, **kwargs, file=self.__stderrStream)

    def fail(self, *args, returncode=1, **kwargs):
        what = self.error(*args, **kwargs)
        raise InvocationError(returncode, what)

    def joinPath(self, *paths):
        return os.path.join(self.__cwd, *paths)

    def getStdio(self):
        return self.__stdioBuffer.getvalue().decode(
            locale.getpreferredencoding(), 'replace')

    def setMakeParameters(self, fds, jobs):
        self.__makeFds = fds
        self.__makeJobs = jobs

    async def runInExecutor(self, func, *args):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.__executor, func, *args)
