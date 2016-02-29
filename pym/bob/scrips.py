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

from . import BOB_VERSION
from .errors import BobError
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

def __jenkins(*args, **kwargs):
     from .cmds.jenkins import doJenkins
     doJenkins(*args, **kwargs)

def __ls(*args, **kwargs):
     from .cmds.misc import doLS
     doLS(*args, **kwargs)

availableCommands = {
    "build"  : (__build, "Build (sub-)packages in release mode"),
    "dev"        : (__develop, "Build (sub-)packages in development mode"),
    "clean"  : (__clean, "Delete unused src/build/dist paths"),
    "jenkins" : (__jenkins, "Configure Jenkins server"),
    "ls"         : (__ls, "List package hierarchy"),
}

def doHelp(extended, fd):
    cmds = "\n".join(sorted([ "  {:16s}{}".format(k, v[1]) for (k,v) in availableCommands.items() ]))
    print("usage: bob [-h | --help] [--version] <command> [<args>]", file=fd)
    if extended:
        print("\nThe following commands are available:", file=fd)
        print("\n{}\n".format(cmds), file=fd)
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
        if len(sys.argv) > 1:
            verb = sys.argv[1]
            argv = sys.argv[2:]
            if verb in availableCommands:
                from .input import RecipeSet
                recipes = RecipeSet()
                recipes.parse()
                availableCommands[verb][0](recipes, argv, bobRoot)
            elif (verb == '-h') or (verb == '--help'):
                doHelp(True, sys.stdout)
            elif (verb == '--version'):
                print("Bob version", BOB_VERSION)
            else:
                print("Don't know what to do for '{}'.".format(verb), file=sys.stderr)
                ret = 2
                doHelp(True, sys.stderr)
        else:
            doHelp(False, sys.stderr)

    except BobError as e:
        print(e)
        ret = 1
    except KeyboardInterrupt:
        ret = 2
    except Exception:
        print(colorize("""An internal Exception has occured. This should not have happenend.
Please open an issue at https://github.com/BobBuildTool/bob with the following backtrace:""", "31;1"), file=sys.stderr)
        traceback.print_exc(file=sys.stdout)
        ret = 3
    finally:
        sys.stdout = origSysStdOut
        sys.stderr = origSysStdErr

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

