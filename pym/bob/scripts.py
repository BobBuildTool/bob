# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

from . import BOB_VERSION, _enableDebug, DEBUG
from .errors import BobError
from .state import finalize
from .tty import colorize, Unbuffered, setColorMode, cleanup
from .utils import asHexStr, hashPath, getPlatformTag, EventLoopWrapper
import argparse
import logging
import sys
import traceback
import os

def __archive(*args, **kwargs):
     from .cmds.archive import doArchive
     doArchive(*args, **kwargs)
     return 0

def __build(*args, **kwargs):
     from .cmds.build.build import doBuild
     doBuild(*args, **kwargs)
     return 0

def __develop(*args, **kwargs):
     from .cmds.build.build import doDevelop
     doDevelop(*args, **kwargs)
     return 0

def __clean(*args, **kwargs):
     from .cmds.build.clean import doClean
     doClean(*args, **kwargs)
     return 0

def __graph(*args, **kwargs):
    from .cmds.graph import doGraph
    doGraph(*args, **kwargs)
    return 0

def __help(*args, **kwargs):
    from .cmds.help import doHelp
    doHelp(availableCommands.keys(), *args, **kwargs)
    return 0

def __init(*args, **kwargs):
    from .cmds.misc import doInit
    doInit(*args, **kwargs)
    return 0

def __jenkins(*args, **kwargs):
     from .cmds.jenkins.jenkins import doJenkins
     doJenkins(*args, **kwargs)
     return 0

def __ls(*args, **kwargs):
     from .cmds.misc import doLS
     doLS(*args, **kwargs)
     return 0

def __layers(*args, **kwargs):
     from .cmds.layers import doLayers
     doLayers(*args, **kwargs)
     return 0

def __project(*args, **kwargs):
     from .cmds.build.project import doProject
     doProject(*args, **kwargs)
     return 0

def __show(*args, **kwars):
     from .cmds.show import doShow
     doShow(*args, **kwars)
     return 0

def __status(*args, **kwars):
     from .cmds.build.status import doStatus
     doStatus(*args, **kwars)
     return 0

def __queryscm(*args, **kwargs):
     from .cmds.misc import doQuerySCM
     doQuerySCM(*args, **kwargs)
     return 0

def __querymeta(*args, **kwargs):
     from .cmds.misc import doQueryMeta
     doQueryMeta(*args, **kwargs)
     return 0

def __queryrecipe(*args, **kwargs):
     from .cmds.misc import doQueryRecipe
     doQueryRecipe(*args, **kwargs)
     return 0

def __querypath(*args, **kwargs):
     from .cmds.build.query import doQueryPath
     doQueryPath(*args, **kwargs)
     return 0

def __invoke(*args, **kwargs):
    from .cmds.invoke import doInvoke
    return doInvoke(*args, **kwargs)

def __jenkinsExecute(*args, **kwargs):
    from .cmds.jenkins.exec import doJenkinsExecute
    return doJenkinsExecute(*args, **kwargs)

availableCommands = {
    "archive"       : ('hl', __archive, "Manage binary artifact archives"),
    "build"         : ('hl', __build, "Build (sub-)packages in release mode"),
    "dev"           : ('hl', __develop, "Build (sub-)packages in development mode"),
    "clean"         : ('hl', __clean, "Delete unused src/build/dist paths of release builds"),
    "graph"         : ('hl', __graph, "Make a interactive dependency graph"),
    "help"          : ('hl', __help, "Display help information about command"),
    "init"          : ('hl', __init, "Initialize build tree"),
    "jenkins"       : ('hl', __jenkins, "Configure Jenkins server"),
    "layers"        : ('hl', __layers, "Handle layers"),
    "ls"            : ('hl', __ls, "List package hierarchy"),
    "project"       : ('hl', __project, "Create project files"),
    "show"          : ('hl', __show, "Show properties of a package"),
    "status"        : ('hl', __status, "Show SCM status"),

    "query-scm"     : ('ll', __queryscm, "Query SCM information"),
    "query-recipe"  : ('ll', __queryrecipe, "Query package sources"),
    "query-path"    : ('ll', __querypath, "Query path information"),
    "query-meta"    : ('ll', __querymeta, "Query Package meta information"),

    "_invoke"       : (None, __invoke, ""),
    "_jexec"        : (None, __jenkinsExecute, "")
}

def describeCommands():
    hlCmds = "\n".join(sorted([ "  {:16s}{}".format(k, v[2])
        for (k,v) in availableCommands.items() if v[0] == 'hl' ]))
    llCmds = "\n".join(sorted([ "  {:16s}{}".format(k, v[2])
        for (k,v) in availableCommands.items() if v[0] == 'll' ]))
    return """
The following high level commands are available:

{}

The following scripting commands are available:

{}

See 'bob <command> -h' for more information on a specific command.""".format(hlCmds, llCmds);

def catchErrors(fun, *args, **kwargs):
    try:
        ret = fun(*args, **kwargs)
    except BrokenPipeError:
        # explicitly close stderr to suppress further error messages
        sys.stderr.close()
        ret = 0
    except BobError as e:
        print(e, file=sys.stderr)
        ret = e.returncode
    except KeyboardInterrupt:
        ret = 2
    except ImportError as e:
        if e.name:
            print(colorize("Python module '{}' seems to be missing. ".format(e.name) +
                           "Please check your installation...", "31;1"),
                  file=sys.stderr)
        else:
            print(colorize(str(e) + " Please check your installation...", "31;1"),
                  file=sys.stderr)
        ret = 3
    except Exception:
        print(colorize("""An internal Exception has occured. This should not have happenend.
Please open an issue at https://github.com/BobBuildTool/bob with the following backtrace:""", "31;1"), file=sys.stderr)
        print("Bob version", BOB_VERSION, file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        ret = 3

    return ret

def bob(bobRoot = None):
    if not bobRoot:
        bobRoot = os.path.realpath(os.path.abspath(sys.argv[0]))
    origSysStdOut = sys.stdout
    origSysStdErr = sys.stderr
    logging.disable(logging.ERROR)

    # Prevent any buffering. Even on a tty Python is doing line buffering.
    sys.stdout = Unbuffered(sys.stdout)
    sys.stderr = Unbuffered(sys.stderr)

    def cmd():
        parser = argparse.ArgumentParser(prog="bob",
                                         description="Bob build tool\n" + describeCommands(),
                                         formatter_class=argparse.RawDescriptionHelpFormatter)
        parser.add_argument('-C', '--directory', dest='directory', action='append', help="Change to DIRECTORY before doing anything", metavar="DIRECTORY")
        parser.add_argument('--version', dest='version', action='store_true', help="Show version")
        parser.add_argument('--debug',   dest='debug', help="Enable debug modes (audit,pkgck,ngd,prof)")
        parser.add_argument('-i', dest='ignore_commandCfg', default=False, action='store_true',
                help="Use bob's default argument settings and do not use commands section of the userconfig.")
        parser.add_argument('--color', dest='color_mode',
                help="Color mode of console output (default: auto)",
                choices=['never', 'always', 'auto'])
        parser.add_argument("--query", dest='query_mode', metavar="MODE",
                choices=['nullset', 'nullglob', 'nullfail'],
                help="Handling of emtpy queries (default: nullglob)")
        parser.add_argument('command', nargs='?', help="Command to execute")
        parser.add_argument('args', nargs=argparse.REMAINDER, help="Arguments to command")

        args = parser.parse_args(sys.argv[1:])
        if args.version:
            print("Bob version", BOB_VERSION)
            return 0

        if args.debug:
            _enableDebug(args.debug)

        if args.ignore_commandCfg:
            from .input import RecipeSet
            RecipeSet.ignoreCommandCfg()

        if args.color_mode:
            from .input import RecipeSet
            RecipeSet.setColorModeCfg(args.color_mode)
            setColorMode(args.color_mode)

        if args.query_mode:
            from .input import RecipeSet
            RecipeSet.setQueryMode(args.query_mode)

        if args.command is None:
            print("No command specified. Use 'bob -h' for help.", file=sys.stderr)
            return 2

        # Shortcut for 'bob help' displaying the same help screen like 'bob
        # --help' would do. The 'bob help COMMAND' case is handled by help from
        # the available commands.
        if args.command == "help" and len(args.args) == 0:
            parser.print_help()
            return 0

        if args.command in availableCommands:
            if args.directory is not None:
                for i in args.directory:
                    try:
                        os.chdir(i)
                    except OSError as e:
                        print("bob -C: unable to change directory:", str(e), file=sys.stderr)
                        return 1
            cmd = availableCommands[args.command][1]
            if DEBUG['prof']:
                import cProfile, pstats
                pr = cProfile.Profile()
                pr.enable()
                ret = cmd(args.args, bobRoot)
                pr.disable()
                ps = pstats.Stats(pr, stream=sys.stderr).sort_stats('tottime')
                print("Bob", BOB_VERSION, "profile:", file=sys.stderr)
                print("Args:", sys.argv[1:], file=sys.stderr)
                ps.print_stats()
            else:
                ret = cmd(args.args, bobRoot)
            return ret
        else:
            print("Don't know what to do for '{}'. Use 'bob -h' for help".format(args.command), file=sys.stderr)
            return 2

    try:
        ret = catchErrors(cmd)
    finally:
        cleanup()
        sys.stdout = origSysStdOut
        sys.stderr = origSysStdErr
        finalize()

    return ret

if __name__ == '__main__':
    if sys.argv[1] == 'bob':
        rootDir = sys.argv[2]
        del sys.argv[1:3]
        sys.exit(bob(rootDir))

