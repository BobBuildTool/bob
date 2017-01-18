# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from . import BOB_VERSION, _enableDebug
from .errors import BobError
from .state import finalize
from .tty import colorize, Unbuffered
from .utils import asHexStr, hashDirectory
import argparse
import sys
import traceback

def __build(*args, **kwargs):
     from .cmds.build import doBuild
     doBuild(*args, **kwargs)

def __develop(*args, **kwargs):
     from .cmds.build import doDevelop
     doDevelop(*args, **kwargs)

def __clean(*args, **kwargs):
     from .cmds.build import doClean
     doClean(*args, **kwargs)

def __help(*args, **kwargs):
    from .cmds.help import doHelp
    doHelp(availableCommands.keys(), *args, **kwargs)

def __jenkins(*args, **kwargs):
     from .cmds.jenkins import doJenkins
     doJenkins(*args, **kwargs)

def __ls(*args, **kwargs):
     from .cmds.misc import doLS
     doLS(*args, **kwargs)

def __project(*args, **kwargs):
     from .cmds.build import doProject
     doProject(*args, **kwargs)

def __status(*args, **kwars):
     from .cmds.build import doStatus
     doStatus(*args, **kwars)

def __queryscm(*args, **kwargs):
     from .cmds.misc import doQuerySCM
     doQuerySCM(*args, **kwargs)

def __queryrecipe(*args, **kwargs):
     from .cmds.misc import doQueryRecipe
     doQueryRecipe(*args, **kwargs)

def __querypath(*args, **kwargs):
     from .cmds.build import doQueryPath
     doQueryPath(*args, **kwargs)

availableCommands = {
    "build"         : (True, __build, "Build (sub-)packages in release mode"),
    "dev"           : (True, __develop, "Build (sub-)packages in development mode"),
    "clean"         : (True, __clean, "Delete unused src/build/dist paths of release builds"),
    "help"          : (True, __help, "Display help information about command"),
    "jenkins"       : (True, __jenkins, "Configure Jenkins server"),
    "ls"            : (True, __ls, "List package hierarchy"),
    "project"       : (True, __project, "Create project files"),
    "status"        : (True, __status, "Show SCM status"),

    "query-scm"     : (False, __queryscm, "Query SCM information"),
    "query-recipe"  : (False, __queryrecipe, "Query package sources"),
    "query-path"    : (False, __querypath, "Query path information"),
}

def doHelp(extended, fd):
    hlCmds = "\n".join(sorted([ "  {:16s}{}".format(k, v[2])
        for (k,v) in availableCommands.items() if v[0] ]))
    llCmds = "\n".join(sorted([ "  {:16s}{}".format(k, v[2])
        for (k,v) in availableCommands.items() if not v[0] ]))
    print("usage: bob [-h | --help] [--version] <command> [<args>]", file=fd)
    if extended:
        print("\nThe following high level commands are available:", file=fd)
        print("\n{}\n".format(hlCmds), file=fd)
        print("The following scripting commands are available:", file=fd)
        print("\n{}\n".format(llCmds), file=fd)
        print("See 'bob <command> -h' for more information on a specific command.", file=fd)

def bob(bobRoot):
    ret = 0
    origSysStdOut = sys.stdout
    origSysStdErr = sys.stderr

    # prevent buffering if we are not on a tty
    if not sys.stdout.isatty():
        sys.stdout = Unbuffered(sys.stdout)
    if not sys.stderr.isatty():
        sys.stderr = Unbuffered(sys.stderr)

    try:
        while len(sys.argv) > 1:
            verb = sys.argv[1]
            argv = sys.argv[2:]
            if verb in availableCommands:
                availableCommands[verb][1](argv, bobRoot)
            elif (verb == '-h') or (verb == '--help'):
                doHelp(True, sys.stdout)
            elif (verb == '--version'):
                print("Bob version", BOB_VERSION)
            elif verb == "--debug":
                _enableDebug()
                del sys.argv[1]
                continue
            else:
                print("Don't know what to do for '{}'.".format(verb), file=sys.stderr)
                ret = 2
                doHelp(True, sys.stderr)
            break
        else:
            doHelp(False, sys.stderr)

    except BrokenPipeError:
        # explicitly close stderr to suppress further error messages
        sys.stderr.close()
    except BobError as e:
        print(e, file=sys.stderr)
        ret = 1
    except KeyboardInterrupt:
        ret = 2
    except ImportError as e:
        print(colorize("Python module '{}' seems to be missing. ".format(e.name) +
                       "Please check your installation...", "31;1"),
              file=sys.stderr)
        ret = 3
    except Exception:
        print(colorize("""An internal Exception has occured. This should not have happenend.
Please open an issue at https://github.com/BobBuildTool/bob with the following backtrace:""", "31;1"), file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        ret = 3
    finally:
        sys.stdout = origSysStdOut
        sys.stderr = origSysStdErr
        finalize()

    return ret

def hashTree():
    parser = argparse.ArgumentParser(description="""Calculate hash sum of a directory.
        To speed up repeated hashing of the same directory specify a state cache
        with '-s'. This cache holds the calculated file caches. Unmodified files
        will not be read again in subsequent runs.""")
    parser.add_argument('-s', '--state', help="State cache path")
    parser.add_argument('dir', help="Directory")
    args = parser.parse_args()

    digest = hashDirectory(args.dir, args.state)
    print(asHexStr(digest))
    return 0

def hashEngine():
    parser = argparse.ArgumentParser(description="Create hash based on spec.")
    parser.add_argument('-o', dest="output", metavar="OUTPUT", default="-", help="Output file (default: stdout)")
    parser.add_argument('--state', help="State cache directory")
    parser.add_argument('spec', nargs='?', default="-", help="Spec input (default: stdin)")
    args = parser.parse_args()

    if args.spec == "-":
        inFile = sys.stdin
    else:
        inFile = open(args.spec, "r")

    l = inFile.readline().strip()
    try:
        res = __process(l, inFile, args.state)
        if args.output == "-":
            sys.stdout.buffer.write(res)
        else:
            with open(args.output, "wb") as f:
                f.write(res)
    except OSError as e:
        print("IO error:", str(e), file=sys.stderr)
        return 1

    return 0

def __process(l, inFile, stateDir):
    if l.startswith("="):
        return bytes.fromhex(l[1:])
    elif l.startswith("<"):
        with open(l[1:], "rb") as f:
            return f.read()
    elif l.startswith("{"):
        import hashlib
        return __processBlock(hashlib.new(l[1:]), inFile, stateDir)
    elif l.startswith("#"):
        import os.path
        if stateDir:
            stateFile = os.path.join(stateDir, l[1:].replace(os.sep, "_"))
        else:
            stateFile = None
        return hashDirectory(l[1:], stateFile)
    else:
        print("Malformed spec:", l, file=sys.stderr)
        sys.exit(1)

def __processBlock(h, inFile, stateDir):
    while True:
        l = inFile.readline().strip()
        if l.startswith("}"):
            return h.digest()
        else:
            h.update(__process(l, inFile, stateDir))

if __name__ == '__main__':
    if sys.argv[1] == 'bob':
        rootDir = sys.argv[2]
        del sys.argv[1:3]
        sys.exit(bob(rootDir))

