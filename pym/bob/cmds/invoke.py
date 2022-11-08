# Bob Build Tool
# Copyright (C) 2019  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ..errors import BuildError
from ..invoker import Invoker, InvocationMode
from ..languages import StepSpec
from ..utils import EventLoopWrapper
import argparse
import sys


def doInvoke(argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob _invoke",
        description="Invoke a single step.")
    parser.add_argument('spec', help="The step spec file")
    parser.add_argument('mode', default='run', choices=['run', 'update', 'shell', 'fingerprint'],
        nargs='?', help="Invocation mode")

    group = parser.add_mutually_exclusive_group()
    group.add_argument('--clean', '-c', action='store_true', default=False,
        help="Clean workspace before execution")
    group.add_argument('--incremental', '-i', action='store_false', dest='clean',
        help="Do not clean workspace before execution")

    parser.add_argument('--keep-sandbox', '-k', action='store_true',
        help="Keep sandbox after execution")
    parser.add_argument('-E', dest="preserve_env", default=False, action='store_true',
        help="Preserve whole environment")
    parser.add_argument('-n', '--no-logfiles', default=False, action='store_true',
        help="Disable log file generation.")
    parser.add_argument('-q', '--quiet', default=0, action='count',
        help="Decrease verbosity (may be specified multiple times)")
    parser.add_argument('-v', '--verbose', default=0, action='count',
        help="Increase verbosity (may be specified multiple times)")

    args = parser.parse_args(argv)
    verbosity = min(3, max(0, 1 + args.verbose - args.quiet)) # [0..4], default: 1

    try:
        with open(args.spec) as f:
            spec = StepSpec.fromFile(f)
    except OSError as e:
        raise BuildError("Error reading spec: " + str(e))

    # Let's do it...
    with EventLoopWrapper() as (loop, executor):
        if args.mode == 'shell':
            invoker = Invoker(spec, args.preserve_env, True, True, True, False,
                              False, executor=executor)
            ret = loop.run_until_complete(invoker.executeStep(InvocationMode.SHELL,
                args.clean, args.keep_sandbox))
        elif args.mode in ('run', 'update'):
            invoker = Invoker(spec, args.preserve_env, args.no_logfiles,
                verbosity >= 2, verbosity >= 1, verbosity >= 3, False,
                executor=executor)
            ret = loop.run_until_complete(invoker.executeStep(
                InvocationMode.CALL if args.mode == 'run' else InvocationMode.UPDATE,
                args.clean, args.keep_sandbox))
        elif args.mode == 'fingerprint':
            invoker = Invoker(spec, args.preserve_env, True, True, True,
                              verbosity >= 3, False, executor=executor)
            (ret, stdout, stderr) = loop.run_until_complete(invoker.executeFingerprint(args.keep_sandbox))
            if ret == 0:
                sys.stdout.buffer.write(stdout)
            else:
                sys.stderr.buffer.write(stderr)
        else:
            assert False, "not reached"

    # Convert signals to error codes like bash does
    if ret < 0:
        ret = 128 - ret
    return ret
