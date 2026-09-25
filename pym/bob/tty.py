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
COLORS2MARK = [ " ", " ", "✔", "★", "⚠", "✘"]

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

    def suspend(self):
        """Restore tty settings upon SIGTSTP (terminal stop)"""
        self.cleanup()

    def resume(self):
        """Resume tty after SIGTSTP"""
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

def duration2text(duration):
    if duration >= 60*60:
        duration = int(duration)
        return "{:d}h{:02d}m".format(duration // 3600, (duration // 60) % 60)
    elif duration >= 60:
        duration = int(duration)
        return "{:d}m{:02d}s".format(duration // 60, duration % 60)
    else:
        return "{:.1f}s".format(duration)

class ParallelTtyUIAction(BaseTUIAction):
    def __init__(self, tui, job, slot, name, msg, ellipsis, showDetails, getTime):
        super().__init__(showDetails)
        self.__tui = tui
        self.__job = job
        self.__slot = slot
        self.__name = name
        self.__msg = msg
        self.__ellipsis = ellipsis
        if not ellipsis: self.setError("")
        self.__getTime = getTime
        self.startTime = getTime()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is not None:
            kind = ERROR
        else:
            kind = EXECUTED

        msg = self.__msg
        if self.__ellipsis:
            if exc_type is None:
                kind = self.ok_kind
                msg += self.ok_message
            else:
                kind = self.err_kind
                msg += self.err_message

        duration = duration2text(self.__getTime() - self.startTime)
        msg = colorize(COLORS2MARK[kind & 7] + " " + msg, kind)+ " - " + duration

        if not self.__ellipsis and exc_type is not None and self.err_message:
            ruler = colorize("═" * terminalSize.columns, self.err_kind)
            msg = [msg, ruler, self.err_message, ruler]

        self.__tui._putResult(self.__slot, msg)
        return False

class ParallelTtyUI(BaseTUI):
    def __init__(self, verbosity, maxJobs, loop):
        super().__init__(verbosity)
        self.__index = 1
        self.__maxJobs = maxJobs
        self.__jobs = {}
        self.__slots = [None] * maxJobs
        self.__tasksDone = 0
        self.__tasksNum = 1
        self.__loop = loop
        self.__footerLines = 0

        self.__ttyInit()

    def __ttyInit(self):
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

        # Update every 0.1s
        self.__timer = self.__loop.call_later(0.1, self.__timerTick)

    def __timerTick(self):
        # We have to poll for size changes on Windows. :(
        if sys.platform == "win32":
            global terminalSize
            terminalSize = shutil.get_terminal_size()
        self.__putFooter()
        self.__timer = self.__loop.call_later(0.1, self.__timerTick)

    def __nextJob(self):
        ret = self.__index
        self.__index += 1
        return ret

    def __putLineCont(self, line):
        # Erase every line individually. The text might wrap or span multiple
        # lines which would otherwise leave remnants of the footer behind.
        # Erasing to the end of the screen (ED) must be avoided here: if the
        # cursor is in the top left corner, some terminals (e.g. tmux with
        # "scroll-on-clear") move the whole screen into the history buffer.
        for l in line.split("\n"):
            print("\r\x1b[2K", l, "\x1b[K", sep="")

    def __putLine(self, line):
        self.__putLineCont(line)
        self.__putFooter()

    def __putFooter(self):
        # Take a snapshot. The terminal size might be changed asynchronously
        # by the SIGWINCH handler.
        columns = terminalSize.columns
        rows = terminalSize.lines

        # CR, disable line wrap
        print("\r\x1b[?7l", end="")

        # Print all active jobs. The footer must never be higher than the
        # terminal. Otherwise the top of it would scroll out of the screen.
        maxLines = max(rows - 2, 1)
        active = [ num for num in self.__slots if num is not None ]
        if len(active) > maxLines:
            hidden = len(active) - maxLines + 1
            active = active[:maxLines-1]
        else:
            hidden = 0

        lines = 1
        now = self.__loop.time()
        print("\x1b[2K╭{}╮\n".format("─" * (columns - 2)), end='')
        for num in active:
            action, message = self.__jobs[num]
            duration = duration2text(now - action.startTime)
            fill = " " * (columns - 6 - len(message) - len(duration))
            print("\x1b[2K│ {} - {}{}│\n".format(colorize(message, EXECUTED), duration, fill), end='')
            lines += 1
        if hidden:
            message = "... and {} more".format(hidden)
            print("\x1b[2K│ {}{}│\n".format(message, " " * (columns - 3 - len(message))), end='')
            lines += 1

        status = " {}/{} jobs running, {}% ({}/{} tasks) done ".format(
                    len(self.__jobs), self.__maxJobs,
                    self.__tasksDone*100//self.__tasksNum,
                    self.__tasksDone, self.__tasksNum)
        tailSize = max(columns - 4 - len(status), 0)
        # Erase to the end of the screen to remove stale lines of a previous,
        # larger footer.
        print("\x1b[J╰──{}{}╯".format(status, "─" * tailSize), end="")
        # Move up <lines> lines, enable line wrap
        print("\x1b[{}A".format(lines), "\x1b[?7h\r", sep='', end='')
        self.__footerLines = lines

        # The last line has no newline and is thus still buffered. Flush it
        # so that the cursor is really at the top of the footer. Otherwise
        # anything that is written directly to the terminal (e.g. stderr)
        # would appear at the wrong position.
        sys.stdout.flush()

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
        self.__putLine(colorize("{} {}".format(COLORS2MARK[kind & 7], message), kind))

    def stepMessage(self, step, action, message, kind, severity):
        if not self._isVisible(severity): return
        self.__putLine("  {}".format(colorize(
            "{:10}{} - {}".format(action, step.getPackage().getName(), message), kind)))

    def stepAction(self, step, action, message, severity, details):
        return self.__action(step, action, message, severity, details, True)

    def stepExec(self, step, action, message, severity, details):
        return self.__action(step, action, message, severity, details, False)

    def __action(self, step, action, message, severity, details, ellipsis):
        if not self._isVisible(severity):
            return DummyTUIAction()

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
        msg = "{:10}{} - {}{}".format(action, name, message, details)
        ret = ParallelTtyUIAction(self, job, slot, name, msg, ellipsis, showDetails, self.__loop.time)
        self.__jobs[job] = (ret, "{:10}{} - {}".format(action, name, message))
        self.__putFooter()
        return ret

    def cleanup(self):
        self.__putFooter()
        # Move below the footer and enable cursor
        print("\n" * self.__footerLines, "\x1b[?25h", sep="")
        sys.stdout.flush()
        try:
            import termios
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self.__oldTcAttr)
        except ImportError:
            pass

    def resume(self):
        self.__ttyInit()
        self.__putFooter()

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

        self.__ttyInit()

    def __ttyInit(self):
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
        self.__jobs[job] = "{} {}".format(action, name)
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

    def resume(self):
        self.__ttyInit()
        self.__putFooter()

    def setProgress(self, done, num):
        self.__tasksDone = done
        self.__tasksNum = num

def log(message, kind, severity=ALWAYS):
    __tui.log(message, kind, severity)

def stepMessage(step, action, message, kind, severity=ALWAYS):
    __tui.stepMessage(step, action, message, kind, severity)

def stepAction(step, action, message, severity=ALWAYS, details=""):
    return __tui.stepAction(step, action, message, severity, details)

def stepExec(step, action, message, severity=ALWAYS, details=""):
    return __tui.stepExec(step, action, message, severity, details)

def setVerbosity(verbosity):
    verbosity = max(ALWAYS, min(TRACE, verbosity))
    __tui.setVerbosity(verbosity)

def setProgress(done, num):
    __tui.setProgress(done, num)

def setTui(maxJobs, loop):
    global __tui
    __tui.cleanup()
    if maxJobs <= 1:
        __tui = SingleTUI(__tui.getVerbosity())
    elif __onTTY:
        if maxJobs <= __parallelTUIThreshold:
            __tui = ParallelTtyUI(__tui.getVerbosity(), maxJobs, loop)
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

def handleTerminalStop(signum, frame):
    __tui.suspend()
    signal.raise_signal(signal.SIGSTOP)
    __tui.resume()

def handleWinChange(signal, frame):
    global terminalSize
    terminalSize = shutil.get_terminal_size()

# module initialization

__onTTY = (sys.stdout.isatty() and sys.stderr.isatty())
__useColor = False
__tui = SingleTUI(NORMAL)
__parallelTUIThreshold = 16

if __onTTY:
    # Get (initial) terminal size
    import shutil
    terminalSize = shutil.get_terminal_size()

    if sys.platform == "win32":
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
    else:
        # Intercept SIGTSTP to leave TTY in a sane state if user presses Ctrl+Z.
        import signal
        signal.signal(signal.SIGTSTP, handleTerminalStop)
        signal.signal(signal.SIGWINCH, handleWinChange)


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
