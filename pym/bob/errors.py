# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

from .tty import colorize

class BobError(Exception):
    def __init__(self, slogan, kind=None, stackSlogan="", help="", returncode=1):
        self.kind = (kind + " error: ") if kind is not None else "Error: "
        self.slogan = slogan
        self.stackSlogan = stackSlogan
        self.stack = []
        self.help = help
        self.returncode = returncode

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
    def __init__(self, slogan, *args, **kwargs):
        BobError.__init__(self, slogan, "Parse", "Processing stack", *args, **kwargs)

class BuildError(BobError):
    def __init__(self, slogan, *args, **kwargs):
        BobError.__init__(self, slogan, "Build", "Failed package", *args, **kwargs)


class MultiBobError(BobError):
    def __init__(self, others):
        self.others = []
        for i in others:
            if isinstance(i, MultiBobError):
                for j in i.others:
                    if j not in self.others: self.others.append(j)
            elif isinstance(i, BobError):
                if i not in self.others: self.others.append(i)
            else:
                raise i

    def __str__(self):
        return "\n".join(str(i) for i in self.others)

    def pushFrame(self, frame):
        pass

    def setStack(self, stack):
        pass

    @property
    def returncode(self):
        return max(1, 1, *(i.returncode for i in self.others))

