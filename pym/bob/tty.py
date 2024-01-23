# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

import sys

DEFAULT = 0
SKIPPED = 1
EXECUTED = 2
INFO = 3
WARNING = 4
ERROR = 5
HEADLINE = 6

# The following color codes are only intended for package diffs
ADDED = 7
ADDED_HIGHLIGHT = 8
DELETED = 9
DELETED_HIGHLIGHT = 10

ALWAYS = -2
IMPORTANT = -1
NORMAL = 0
INFO = 1
DEBUG = 2
TRACE = 3

COLORS2CODE = [ "", "", "32", "34", "33", "31", "32;1", "32", "1;32;48;5;22", "31", "1;31;48;5;52" ]
COLORS2TEXT = [ "NOTE", "NOTE", "NOTE", "INFO", "WARN", "ERR ", "====" ]

def colorize(string, color):
    if isinstance(color, int):
        color = COLORS2CODE[color]
    if __useColor and color:
        return "\x1b[" + color + "m" + string + "\x1b[0m"
    else:
        return string

class Unbuffered(object):
    def __init__(self, stream):
        self.stream = stream
    def write(self, data):
        self.stream.write(data)
        self.stream.flush()
    def __getattr__(self, attr):
        return getattr(self.stream, attr)

class Show:
    def __init__(self, slogan, color, message, help, onlyOnce=False):
        self.__slogan = slogan
        self.__color = color
        self.__message = message
        self.__help = help
        self.__triggered = False
        self.__onlyOnce = onlyOnce

    def show(self, location=None):
        if not self.__triggered:
            print(colorize(self.__slogan + ":", self.__color+";1"),
                colorize(((location + ": ") if location else "") + self.__message,
                    self.__color),
                file=sys.stderr)
            if self.__help:
                print(self.__help, file=sys.stderr)
            self.__triggered = self.__onlyOnce

class Info(Show):
    def __init__(self, message, help=None, onlyOnce=False):
        super().__init__("INFO", "34", message, help, onlyOnce)

class InfoOnce(Info):
    def __init__(self, message, help=None):
        super().__init__(message, help, True)

class Warn(Show):
    def __init__(self, message, help=None, onlyOnce=False):
        super().__init__("WARNING", "33", message, help, onlyOnce)

    def warn(self, location=None):
        super().show(location)

class WarnOnce(Warn):
    def __init__(self, message, help=None):
        super().__init__(message, help, True)


###############################################################################

class BaseTUIAction:
    visible = True

    def __init__(self, showDetails):
        self.showDetails = showDetails
        self.ok_kind = EXECUTED
        self.ok_message = "ok"
        self.err_kind = WARNING
        self.err_message = "error"

    def setResult(self, message, kind=EXECUTED, details=""):
        if self.showDetails and details:
            message += " (" + details + ")"
        self.ok_message = message
        self.ok_kind = kind

    def setError(self, message, kind=ERROR, details=""):
        if self.showDetails and details:
            message += " (" + details + ")"
        self.err_message = message
        self.err_kind = kind

    def fail(self, message, kind=ERROR, details=""):
        self.setResult(message, kind, details)
        self.setError(message, kind, details)

class BaseTUI:
    def __init__(self, verbosity):
        self.__verbosity = verbosity

    def getVerbosity(self):
        return self.__verbosity

    def setVerbosity(self, verbosity):
        self.__verbosity = verbosity

    def cleanup(self):
        pass

    def setProgress(self, done, num):
        pass

    def _isVisible(self, severity):
        if isinstance(severity, int):
            return severity <= self.__verbosity
        else:
            low, high = severity
            return (low <= self.__verbosity) and (self.__verbosity <= high)

class DummyTUIAction(BaseTUIAction):
    visible = False

    def __init__(self):
        super().__init__(3)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

class SingleTUIAction(BaseTUIAction):

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            kind = self.ok_kind
            message = self.ok_message
        else:
            kind = self.err_kind
            message = self.err_message
        print(colorize(message, kind))
        return False

class SingleTUI(BaseTUI):
    def __init__(self, verbosity):
        super().__init__(verbosity)
        self.__currentPackage = None

    def __setPackage(self, step):
        package = "/".join(step.getPackage().getStack())
        if package != self.__currentPackage:
            self.__currentPackage = package
            print(">>", colorize(self.__currentPackage, HEADLINE))

    def log(self, message, kind, severity):
        if not self._isVisible(severity): return
        print(colorize("** {}".format(message), kind))

    def stepMessage(self, step, action, message, kind, severity):
        if not self._isVisible(severity): return
        self.__setPackage(step)
        print(colorize("   {:10}{}".format(action, message), kind))

    def stepAction(self, step, action, message, severity, details):
        return self.__action(step, action, message, severity, details, True)

    def stepExec(self, step, action, message, severity, details):
        return self.__action(step, action, message, severity, details, False)

    def __action(self, step, action, message, severity, details, ellipsis):
        if not self._isVisible(severity): return DummyTUIAction()
        self.__setPackage(step)
        showDetails = self._isVisible(INFO)
        if showDetails and details:
            details = " " + details
        else:
            details = ""
        if ellipsis:
            print(colorize("   {:10}{}{} .. ".format(action, message, details), EXECUTED), end="")
            return SingleTUIAction(showDetails)
        else:
            print(colorize("   {:10}{}{}".format(action, message, details), EXECUTED))
            return DummyTUIAction()


class ParallelTtyUIAction(BaseTUIAction):
    def __init__(self, tui, job, slot, name, msg, ellipsis, showDetails):
        super().__init__(showDetails)
        self.__tui = tui
        self.__job = job
        self.__slot = slot
        self.__name = name
        self.__msg = msg
        self.__ellipsis = ellipsis
        if not ellipsis: self.setError("")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is not None:
            kind = ERROR
        else:
            kind = EXECUTED
        msg = "[{:>4}] {}".format(self.__job, colorize(self.__msg, kind))
        if self.__ellipsis:
            if exc_type is None:
                kind = self.ok_kind
                status = self.ok_message
            else:
                kind = self.err_kind
                status = self.err_message
            msg += colorize(status, kind)
        if not self.__ellipsis and exc_type is not None and self.err_message:
            msg = [msg] + [
                "[{:>4}] |{}| {}".format(self.__job, colorize(self.__name, self.err_kind), l)
                for l in self.err_message.split("\n")]
        self.__tui._putResult(self.__slot, msg)
        return False

class ParallelTtyUI(BaseTUI):
    def __init__(self, verbosity, maxJobs):
        super().__init__(verbosity)
        self.__index = 1
        self.__maxJobs = maxJobs
        self.__jobs = {}
        self.__slots = [None] * maxJobs
        self.__tasksDone = 0
        self.__tasksNum = 1

        # disable cursor
        print("\x1b[?25l")

        # disable echo
        try:
            import termios
            fd = sys.stdin.fileno()
            self.__oldTcAttr = termios.tcgetattr(fd)
            new = termios.tcgetattr(fd)
            new[3] = new[3] & ~termios.ECHO
            termios.tcsetattr(fd, termios.TCSADRAIN, new)
        except ImportError:
            pass

    def __nextJob(self):
        ret = self.__index
        self.__index += 1
        return ret

    def __putLineCont(self, line):
        print("\r" + "\x1b[2K", line, "\x1b[K", sep="")

    def __putLine(self, line):
        self.__putLineCont(line)
        self.__putFooter()

    def __putFooter(self):
        # CR, disable line wrap, erase line, ...
        print("\r\x1b[?7l\x1b[2K====== {}/{} jobs running, {}% ({}/{} tasks) done "
                .format(len(self.__jobs), self.__maxJobs,
                        self.__tasksDone*100//self.__tasksNum,
                        self.__tasksDone, self.__tasksNum),
              end="")
        i = 0
        while i < self.__maxJobs:
            num = self.__slots[i]
            if num is not None:
                print("\n\x1b[2K {:>4}  {}".format(num, self.__jobs[num]), end='')
            else:
                print("\n\x1b[2K ****  <idle>", end='')
            i += 1
        # Move up <i> lines, enable line wrap
        print("\x1b[{}A".format(i), "\x1b[?7h\r", sep='', end='')

    def _putResult(self, slot, msg):
        job = self.__slots[slot]
        self.__slots[slot] = None
        del self.__jobs[job]
        if msg:
            if isinstance(msg, list):
                for l in msg: self.__putLineCont(l)
                self.__putFooter()
            else:
                self.__putLine(msg)
        else:
            self.__putFooter()

    def log(self, message, kind, severity):
        if not self._isVisible(severity): return
        print(colorize("[****] {}".format(message), kind))

    def stepMessage(self, step, action, message, kind, severity):
        if not self._isVisible(severity): return
        self.__putLine("[    ] {}".format(colorize(
            "{:10}{} - {}".format(action, step.getPackage().getName(), message), kind)))

    def stepAction(self, step, action, message, severity, details):
        return self.__action(step, action, message, severity, details, True)

    def stepExec(self, step, action, message, severity, details):
        return self.__action(step, action, message, severity, details, False)

    def __action(self, step, action, message, severity, details, ellipsis):
        if not self._isVisible(severity): return DummyTUIAction()
        showDetails = self._isVisible(INFO)
        if showDetails and details:
            details = " " + details
        else:
            details = ""
        if ellipsis:
            details += " .. "

        job = self.__nextJob()
        slot = 0
        while self.__slots[slot] is not None: slot += 1
        name = step.getPackage().getName()
        self.__slots[slot] = job
        self.__jobs[job] = colorize("{:10}{} - {}".format(action, name, message), EXECUTED)
        self.__putFooter()
        msg = "{:10}{} - {}{}".format(action, name, message, details)
        return ParallelTtyUIAction(self, job, slot, name, msg, ellipsis, showDetails)

    def cleanup(self):
        self.__putFooter()
        for i in range(max(len(self.__jobs), self.__maxJobs)+1):
            print()
        print("\x1b[?25h")
        try:
            import termios
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self.__oldTcAttr)
        except ImportError:
            pass

    def setProgress(self, done, num):
        self.__tasksDone = done
        self.__tasksNum = num


class ParallelDumbUIAction(BaseTUIAction):
    def __init__(self, tui, job, name, msg, ellipsis, showDetails):
        super().__init__(showDetails)
        self.__tui = tui
        self.__job = job
        self.__name = name
        self.__msg = msg
        self.__ellipsis = ellipsis
        if not ellipsis: self.setError("")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        kind = EXECUTED if exc_type is None else ERROR
        msg = self.__msg
        stderr = None
        if self.__ellipsis:
            if exc_type is None:
                kind = self.ok_kind
                status = self.ok_message
            else:
                kind = self.err_kind
                status = self.err_message
            msg += status
        elif exc_type is not None and self.err_message:
            kind = self.err_kind
            stderr = self.err_message
        self.__tui._printResult(self.__job, msg, stderr, kind)
        return False

class ParallelDumbUI(BaseTUI):
    def __init__(self, verbosity):
        super().__init__(verbosity)
        self.__index = 1

    def __nextJob(self):
        ret = self.__index
        self.__index += 1
        return ret

    def _print(self, job, msg, kind, stage=""):
        level = COLORS2TEXT[kind & 7]
        print("[{:<5} {:>4}] {}: {}".format(stage, job, level, colorize(msg, kind)))

    def _printResult(self, job, msg, stderr, kind):
        self._print(job, msg, kind, "End")
        if stderr:
            # Print error messages on stderr when being on a dumb output. It is
            # probably redirected by some other script or an analyzed IDE (think
            # "bob project").
            print(stderr, file=sys.stderr)

    def log(self, message, kind, severity):
        if not self._isVisible(severity): return
        self._print("****", message, kind, "*****")

    def stepMessage(self, step, action, message, kind, severity):
        if not self._isVisible(severity): return
        self._print("", "{:10}{} - {}".format(action,
            step.getPackage().getName(), message), kind)

    def stepAction(self, step, action, message, severity, details):
        return self.__action(step, action, message, severity, details, True)

    def stepExec(self, step, action, message, severity, details):
        return self.__action(step, action, message, severity, details, False)

    def __action(self, step, action, message, severity, details, ellipsis):
        if not self._isVisible(severity): return DummyTUIAction()
        showDetails = self._isVisible(INFO)
        if showDetails and details:
            details = " " + details
        else:
            details = ""
        if ellipsis:
            details += ": "

        job = self.__nextJob()
        name = step.getPackage().getName()
        self._print(job, "{:10}{} - {}".format(action, name, message), EXECUTED, "Start")
        msg = "{:10}{} - {}{}".format(action, name, message, details)
        return ParallelDumbUIAction(self, job, name, msg, ellipsis, showDetails)

class MassiveParallelTtyUI(BaseTUI):
    def __init__(self, verbosity, maxJobs):
        super().__init__(verbosity)
        self.__index = 1
        self.__maxJobs = maxJobs
        self.__jobs = {}
        self.__tasksDone = 0
        self.__tasksNum = 1

        # disable cursor
        print("\x1b[?25l")

        # disable echo
        try:
            import termios
            fd = sys.stdin.fileno()
            self.__oldTcAttr = termios.tcgetattr(fd)
            new = termios.tcgetattr(fd)
            new[3] = new[3] & ~termios.ECHO
            termios.tcsetattr(fd, termios.TCSADRAIN, new)
        except ImportError:
            pass

    def __nextJob(self):
        ret = self.__index
        self.__index += 1
        return ret

    def __putLineCont(self, line):
        print("\r" + "\x1b[2K", line, "\x1b[K", sep="")

    def __putLine(self, line):
        self.__putLineCont(line)
        self.__putFooter()

    def __putFooter(self):
        # CR, disable line wrap, erase line, ...
        print("\r\x1b[?7l\x1b[2K====== {}/{} jobs running, {}% ({}/{} tasks) done "
                .format(len(self.__jobs), self.__maxJobs,
                        self.__tasksDone*100//self.__tasksNum,
                        self.__tasksDone, self.__tasksNum))
        for i, name in sorted(self.__jobs.items()):
            print("[{} {}]".format(i, name), end="")
        # Move up one lines, enable line wrap
        print("\x1b[A\x1b[?7h\r", end='')

    def _print(self, job, msg, kind, stage=""):
        self.__putLine("[{:<5} {:>4}] {}".format(stage, job, colorize(msg, kind)))

    def _printResult(self, job, msg, stderr, kind):
        del self.__jobs[job]
        self._print(job, msg, kind, "End")
        if stderr:
            for l in stderr.splitlines():
                self.__putLineCont("[{:<5} {:>4}] {}".format("ERR", job, l))
            self.__putFooter()

    def log(self, message, kind, severity):
        if not self._isVisible(severity): return
        self._print("****", message, kind, "*****")

    def stepMessage(self, step, action, message, kind, severity):
        if not self._isVisible(severity): return
        self._print("", "{:10}{} - {}".format(action,
            step.getPackage().getName(), message), kind)

    def stepAction(self, step, action, message, severity, details):
        return self.__action(step, action, message, severity, details, True)

    def stepExec(self, step, action, message, severity, details):
        return self.__action(step, action, message, severity, details, False)

    def __action(self, step, action, message, severity, details, ellipsis):
        if not self._isVisible(severity): return DummyTUIAction()
        showDetails = self._isVisible(INFO)
        if showDetails and details:
            details = " " + details
        else:
            details = ""
        if ellipsis:
            details += ": "

        job = self.__nextJob()
        name = step.getPackage().getName()
        self.__jobs[job] = name
        self._print(job, "{:10}{} - {}".format(action, name, message), EXECUTED, "Start")
        msg = "{:10}{} - {}{}".format(action, name, message, details)
        return ParallelDumbUIAction(self, job, name, msg, ellipsis, showDetails)

    def cleanup(self):
        self.__putFooter()
        print()
        print("\x1b[?25h")
        try:
            import termios
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self.__oldTcAttr)
        except ImportError:
            pass

    def setProgress(self, done, num):
        self.__tasksDone = done
        self.__tasksNum = num

def log(message, kind, severity=-2):
    __tui.log(message, kind, severity)

def stepMessage(step, action, message, kind, severity=-2):
    __tui.stepMessage(step, action, message, kind, severity)

def stepAction(step, action, message, severity=-2, details=""):
    return __tui.stepAction(step, action, message, severity, details)

def stepExec(step, action, message, severity=-2, details=""):
    return __tui.stepExec(step, action, message, severity, details)

def setVerbosity(verbosity):
    verbosity = max(ALWAYS, min(TRACE, verbosity))
    __tui.setVerbosity(verbosity)

def setProgress(done, num):
    __tui.setProgress(done, num)

def setTui(maxJobs):
    global __tui
    __tui.cleanup()
    if maxJobs <= 1:
        __tui = SingleTUI(__tui.getVerbosity())
    elif __onTTY:
        if maxJobs <= __parallelTUIThreshold:
            __tui = ParallelTtyUI(__tui.getVerbosity(), maxJobs)
        else:
            __tui = MassiveParallelTtyUI(__tui.getVerbosity(), maxJobs)
    else:
        __tui = ParallelDumbUI(__tui.getVerbosity())

def cleanup():
    __tui.cleanup()
    if __onTTY and sys.platform == "win32":
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), __origMode.value)

def ttyReinit():
    """Re-initialize the console settings.

    Work around a MSYS2 odity where the executable unconditionally resets the
    ENABLE_VIRTUAL_TERMINAL_PROCESSING flag even if it was already set when the
    process was started.
    """
    if __onTTY and sys.platform == "win32":
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), __origMode.value | 4)

# module initialization

__onTTY = (sys.stdout.isatty() and sys.stderr.isatty())
__useColor = False
__tui = SingleTUI(NORMAL)
__parallelTUIThreshold = 16

if __onTTY and sys.platform == "win32":
    # Try to set ENABLE_VIRTUAL_TERMINAL_PROCESSING flag. Enables vt100 color
    # codes on Windows 10 console. If this fails we inhibit color code usage
    # because it will clutter the output.
    import ctypes
    import ctypes.wintypes
    __origMode = ctypes.wintypes.DWORD()
    kernel32 = ctypes.windll.kernel32
    kernel32.GetConsoleMode(kernel32.GetStdHandle(-11), ctypes.byref(__origMode))
    if not kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), __origMode.value | 4):
        __onTTY = False

def setColorMode(mode):
    global __useColor
    if mode == 'never':
        __useColor = False
    elif mode == 'always':
        __useColor = True
    elif mode == 'auto':
        __useColor = __onTTY

def setParallelTUIThreshold(num):
    global __parallelTUIThreshold
    __parallelTUIThreshold = num

# auto is the default
setColorMode('auto')
