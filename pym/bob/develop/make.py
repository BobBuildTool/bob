# Bob build tool
# Copyright (C) 2019  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

__all__ = ('makeSandboxHelper', 'makeManpages')

def findYoungestInDir(path, exclude):
    ret = 0
    with os.scandir(path) as entries:
        for entry in entries:
            if entry.name in exclude: continue
            ret = max(ret, entry.stat().st_mtime)
            if entry.is_dir():
                ret = max(ret, findYoungestInDir(entry.path, exclude))
    return ret

def findYoungest(path, exclude=set()):
    if not os.path.exists(path):
        return 0
    ret = os.stat(path).st_mtime
    if os.path.isdir(path):
        ret = max(ret, findYoungestInDir(path, exclude))
    return ret

def getBobRoot():
    return os.path.realpath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))

def makeSandboxHelper():
    # Linux only
    if sys.platform != "linux":
        return

    bobRoot = getBobRoot()
    resultPath = os.path.join(bobRoot, "bin", "bob-namespace-sandbox")
    inputPath = os.path.join(bobRoot, "src", "namespace-sandbox")
    resultDate = findYoungest(resultPath)
    inputDate = findYoungest(inputPath)
    if inputDate > resultDate:
        import subprocess
        print("Build", resultPath, "...", file=sys.stderr)
        sources = [ os.path.join("src", "namespace-sandbox", s)
            for s in ('namespace-sandbox.c', 'network-tools.c', 'process-tools.c') ]
        subprocess.run(["cc", "-o", "bin/bob-namespace-sandbox", "-std=c99",
            "-g"] + sources + ["-lm"], cwd=bobRoot)

    return resultPath

def makeManpages():
    bobRoot = getBobRoot()
    resultPath = os.path.join(bobRoot, "doc", "_build", "man")
    inputPath = os.path.join(bobRoot, "doc")
    resultDate = findYoungest(resultPath)
    inputDate = findYoungest(inputPath, exclude={'_build'})
    if inputDate > resultDate:
        import subprocess
        print("Build manpages in", resultPath, "...", file=sys.stderr)
        subprocess.run(["sphinx-build", "-b", "man", ".", "_build/man"],
            cwd=inputPath)

    return resultPath
