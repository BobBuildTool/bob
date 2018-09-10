# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

import sys

def colorize(string, color):
    if __onTTY:
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

# module initialization

__onTTY = False

def setColorMode(mode):
    global __onTTY
    if mode == 'never':
        __onTTY = False
    elif mode == 'always':
        __onTTY = True
    elif mode == 'auto':
        if sys.stdout.isatty() and sys.stderr.isatty():
            __onTTY = True
        else:
            __onTTY = False
# auto is the default
setColorMode('auto')
