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
from .errors import BuildError, ParseError
from .utils import asHexStr, colorize, Unbuffered, hashDirectory
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

    except ParseError as pe:
        print(colorize("Parse error:", "31;1"), str(pe))
        ret = 1
    except BuildError as be:
        print(colorize("Build error:", "31;1"), str(be))
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

