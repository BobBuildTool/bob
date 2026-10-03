# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

from .tty import colorize

class BobError(Exception):
    """Base class of all errors raised by Bob.

    A ``BobError`` carries a human readable description of what went wrong
    (the ``slogan``), an optional stack of locations that led to the error
    and an optional hint on how to resolve the issue. Bob catches these
    exceptions at the top level, prints them nicely formatted to the user and
    aborts with the given ``returncode``.

    Plugins may raise a ``BobError`` (or, preferably, one of its more
    specific subclasses :class:`bob.errors.ParseError` and
    :class:`bob.errors.BuildError`) to signal an error condition to the user.

    :param slogan: Human readable description of the error.
    :type slogan: str
    :param kind: Short prefix that is shown in front of the slogan, e.g.
        ``"Parse"`` or ``"Build"``. If ``None`` a generic "Error" prefix is
        used.
    :type kind: str | None
    :param stackSlogan: Caption that is shown in front of ``stack``, e.g.
        "Processing stack" or "Failed package".
    :type stackSlogan: str
    :param help: Additional hint that is appended to the error message.
    :type help: str
    :param returncode: Process exit code that Bob shall use if this error
        propagates to the top level uncaught.
    :type returncode: int
    """

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

class ParseError(BobError):
    """Error while parsing recipes, classes or other configuration input.

    Raise this exception to signal that some input could not be parsed or is
    otherwise invalid, e.g. from within a
    :meth:`bob.input.PluginProperty.validate` implementation or a string
    function.

    :param slogan: Human readable description of the error.
    :type slogan: str
    """

    def __init__(self, slogan, *args, **kwargs):
        BobError.__init__(self, slogan, "Parse", "Processing stack", *args, **kwargs)

    def pushFrame(self, frame):
        """Add a location to the processing stack.

        Called while the error propagates up through nested recipe or class
        includes to record where it was raised. The stack is shown with the
        outermost frame first.

        :param frame: Name of the file, recipe or class that was being
            processed.
        :type frame: str
        """
        if not self.stack or (self.stack[0] != frame):
            self.stack.insert(0, frame)

    def setPath(self, path):
        """Set the file that caused the error.

        :param path: Path of the offending file.
        :type path: str
        """
        self.stackSlogan = "Offending file"
        self.stack = [path]

class BuildError(BobError):
    """Error during the execution of a package build step.

    Raise this exception, e.g. from a plugin hook, to signal that building a
    package failed.

    :param slogan: Human readable description of the error.
    :type slogan: str
    """

    def __init__(self, slogan, *args, **kwargs):
        BobError.__init__(self, slogan, "Build", "Failed package", *args, **kwargs)

    def setStack(self, stack):
        """Set the package stack of the package that failed.

        :param stack: Package path stack, e.g. as returned by
            :meth:`bob.input.Package.getStack`, of the failed package.
        :type stack: list[str]
        """
        if not self.stack: self.stack = stack[:]


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

