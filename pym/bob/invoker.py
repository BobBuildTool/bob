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
from pipes import quote
import asyncio
import concurrent.futures
import datetime
import io
import locale
import os
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
            self.__stdOut.write(data)
        elif fd == 2:
            self.__stdErr.write(data)


class InvocationMode(Enum):
    CALL = 'call'
    SHELL = 'shell'

class Invoker:
    def __init__(self, spec, preserveEnv, noLogFiles, showStdOut, showStdErr, trace, redirect):
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
        self.__trace = trace
        self.__sandboxHelperPath = None
        self.__stdioBuffer = io.BytesIO() if redirect else None

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

    def __openLog(self):
        # Create log file
        if self.__logFileName:
            self.__logFile = open(self.__logFileName, "ab", buffering=0)
            self.__logFile.write("### START: {}\n"
                .format(datetime.datetime.now().ctime())
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
                           **kwargs):
        cmd = " ".join(quote(a) for a in args)
        self.trace(cmd)
        if env is None:
            env = self.__env
        else:
            _env = self.__env.copy()
            _env.update(env)
            env = _env

        if stdout == True:
            # If stdout should be captured we use a dedicated buffer. This
            # buffer is then returned to the caller at child exit.
            stdoutRedir = subprocess.PIPE
            stdoutStream = io.BytesIO()
        elif stdout == False:
            stdoutRedir = subprocess.DEVNULL
            stdoutStream = DEVNULL
        else:
            stdoutRedir = self.__stdout
            stdoutStream = self.__stdoutStream

        if stderr == True:
            # If stderr should be captured we use a dedicated buffer. This
            # buffer is then returned to the caller at child exit.
            stderrRedir = subprocess.PIPE
            stderrStream = io.BytesIO()
        elif stdout == False:
            stderrRedir = subprocess.DEVNULL
            stderrStream = DEVNULL
        else:
            stderrRedir = self.__stderr
            stderrStream = self.__stderrStream

        # Sanity check on Windows that there are no environment variables that
        # differ only in case. The Windows envrionment is used case insensitive
        # by libc getenv() but the kernel passes the variables as-is.
        if isWindows():
            duplicates = []
            old = ""
            for i in sorted(env.keys(), key=str.upper):
                if i.upper() == old.upper():
                    duplicates.append((old, i))
                old = i
            if duplicates:
                self.fail("Colliding environment variables:",
                    ", ".join("{} ~= {}".format(i, j) for i,j in duplicates))

        loop = asyncio.get_event_loop()
        exitFuture = asyncio.Future()
        try:
            transport, protocol = await loop.subprocess_exec(
                lambda: LogWriteProtocol(exitFuture, self.__logFile,
                                         stdoutStream, stderrStream),
                *args,
                stdin=self.__stdin, stdout=stdoutRedir, stderr=stderrRedir,
                env=env, cwd=cwd,
                **kwargs)
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
                stdoutStream.seek(0)
                stdoutBuf = io.TextIOWrapper(stdoutStream).read()
            else:
                stdoutBuf = stdoutStream.getvalue()
        else:
            stdoutBuf = None

        if stderr == True:
            if universal_newlines:
                stderrStream.seek(0)
                stderrBuf = io.TextIOWrapper(stderrStream).read()
            else:
                stderrBuf = stderrStream.getvalue()
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

            # setup script and arguments
            if mode == InvocationMode.SHELL:
                realScriptFile, execScriptFile, callArgs = self.__spec.language.setupShell(
                    self.__spec, tmpDir, self.__preserveEnv)
            elif mode == InvocationMode.CALL:
                realScriptFile, execScriptFile, callArgs = self.__spec.language.setupCall(
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
                ret = await self.callCommand(cmdArgs)
            elif mode == InvocationMode.CALL:
                for scm in self.__spec.preRunCmds:
                    scm = getScm(scm)
                    try:
                        await scm.invoke(self)
                    except CmdFailedError as e:
                        self.error(scm.getSource(), "failed")
                        self.error(e.what)
                        raise
                    except Exception:
                        self.error(scm.getSource(), "failed")
                        raise
                await self.checkCommand(cmdArgs)
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
            self.__openLog()

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

            cmdArgs += self.__spec.language.setupFingerprint(self.__spec, env)
            proc = await self.__runCommand(cmdArgs, tmpDir, env=env,
                universal_newlines=False, stdout=True, stderr=True)
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

    async def callCommand(self, args, cwd=None, **kwargs):
        cwd = os.path.join(self.__cwd, cwd) if cwd else self.__cwd
        ret = await self.__runCommand(args, cwd, **kwargs)
        return ret.returncode

    async def checkCommand(self, args, cwd=None, **kwargs):
        cwd = os.path.join(self.__cwd, cwd) if cwd else self.__cwd
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
            locale.getpreferredencoding(), 'surrogateescape')

