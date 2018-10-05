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
HEADLINE = 8

ALWAYS = -2
IMPORTANT = -1
NORMAL = 0
INFO = 1
DEBUG = 2
TRACE = 3

COLORS2CODE = [ "", "", "32", "34", "33", "31" ]

def colorize(string, color):
    if isinstance(color, int):
        color = COLORS2CODE[color & 7] + (";1" if color >= HEADLINE else "")
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

    def _isVisible(self, severity):
        if isinstance(severity, int):
            return severity <= self.__verbosity
        else:
            low, high = severity
            return (low <= self.__verbosity) and (self.__verbosity <= high)

class DummyTUIAction(BaseTUIAction):
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
            print(">>", colorize(self.__currentPackage, EXECUTED|HEADLINE))

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

def cleanup():
    __tui.cleanup()

# module initialization

__onTTY = (sys.stdout.isatty() and sys.stderr.isatty())
__useColor = False
__tui = SingleTUI(NORMAL)

def setColorMode(mode):
    global __useColor
    if mode == 'never':
        __useColor = False
    elif mode == 'always':
        __useColor = True
    elif mode == 'auto':
        __useColor = __onTTY

# auto is the default
setColorMode('auto')
