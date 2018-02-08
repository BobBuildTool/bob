# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

from .tty import colorize

class BobError(Exception):
    def __init__(self, slogan, kind=None, stackSlogan="", help=""):
        self.kind = (kind + " error: ") if kind is not None else "Error: "
        self.slogan = slogan
        self.stackSlogan = stackSlogan
        self.stack = []
        self.help = help

    def __str__(self):
        ret = colorize(self.kind, "31;1") + colorize(self.slogan, "31")
        if self.stack:
            ret = ret + "\n" + self.stackSlogan + ": " + "/".join(self.stack)
        if self.help:
            ret = ret + "\n" + self.help
        return ret

    def pushFrame(self, frame):
        if not self.stack or (self.stack[0] != frame):
            self.stack.insert(0, frame)

    def setStack(self, stack):
        if not self.stack: self.stack = stack[:]

class ParseError(BobError):
    def __init__(self, slogan, help=""):
        BobError.__init__(self, slogan, "Parse", "Processing stack", help)

class BuildError(BobError):
    def __init__(self, slogan, help=""):
        BobError.__init__(self, slogan, "Build", "Failed package", help)

