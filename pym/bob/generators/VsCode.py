# Bob build tool
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Generator for VS Code project files

import argparse
import sys
import re
import os
import shutil
import stat
import xml.etree.ElementTree
import shutil
from os.path import expanduser
from os.path import join
from bob.utils import removePath, isWindows
from bob.errors import BuildError
from bob.utils import summonMagic, hashFile
from collections import OrderedDict, namedtuple
from bob.tty import colorize
from shlex import quote

# helper to get linux or windows (MSYS2 support) cwd
pwd = None
def cwd():
    global pwd
    if pwd == None and isWindows():
        pwd = os.popen('pwd -W').read().strip()
    return os.getcwd() if not isWindows() else pwd

# scan package recursivelely with its dependencies and build a list of checkout dirs
def getCheckOutDirs(package, excludes, dirs):
    def collect(package, excludes, dirs, steps, processed):
        if package._getId() in processed:
            return
        processed.add(package._getId())
        if package.getCheckoutStep().isValid():
            if package.getCheckoutStep().getVariantId() not in steps:
                steps.add(package.getCheckoutStep().getVariantId())
                dirs.append([package.getName(), package.getCheckoutStep().getWorkspacePath()])

        for d in package.getDirectDepSteps():
            excluded = False
            for e in excludes:
                if (e.search(d.getPackage().getName())):
                    excluded = True
                    break

            if not excluded:
                collect(d.getPackage(), excludes, dirs, steps, processed)

    collect(package, excludes, dirs, set(), set())

def generateFile(entries, fileName):
    try:
        os.remove(fileName)
    except OSError:
        pass
    fileName = open(fileName, "w")
    for e in entries:
        fileName.write(e + "\n")
    fileName.close()

def addRunSteps(outFile, runTargets):
    if runTargets:
        print("\nNot implemented: {} add run-targets {}".format(outFile.name, runTargets))

RunStep = namedtuple("RunStep", "dir cmd")

def addBuildConfig(outFile, num, name, buildArgs, buildMeFile):
    outFile.write("    {\n")
    outFile.write("      \"label\": \"{}\",\n".format(name))
    outFile.write("      \"type\": \"process\",\n")
    if isWindows():
        if os.getenv('WD') is None:
            raise BuildError("Cannot create QtCreator project for Windows! MSYS2 must be started by msys2_shell.cmd script!")
        outFile.write("      \"command\": \"{}\",\n".format(os.path.normpath(os.path.join(os.getenv('WD').replace('\\', '/'), "sh.exe"))))
    else:
        outFile.write("      \"command\": \"sh\",\n")
    outFile.write("      \"args\": [\n")
    outFile.write("        \"{}\",\n".format(buildMeFile))
    outFile.write("        \"-v\",\n")
    for i in buildArgs.split(' '):
        outFile.write("        \"{}\",\n".format(i))
    outFile.write("      ],\n")
    outFile.write("      \"group\": {\n")
    outFile.write("        \"kind\": \"build\",\n")
    outFile.write("        \"isDefault\": true\n")
    outFile.write("      }\n")
    outFile.write("    },\n")

def addBuildSteps(outFile, buildMeFile, buildConfigs):
    outFile.write("{\n")
    outFile.write("  \"version\": \"2.0.0\",\n")
    outFile.write("  \"options\":{\n")
    if isWindows():
        outFile.write("    \"shell\":{\n")
        outFile.write("      \"executable\": \"{}\",\n".format(os.path.normpath(os.path.join(os.getenv('WD').replace('\\', '/'), '..', '..', 'msys2_shell.cmd'))))
        outFile.write("      \"args\": [\n")
        outFile.write("        \"-msys2\",\n")
        outFile.write("        \"-defterm\",\n")
        outFile.write("        \"-no-start\",\n")
        outFile.write("        \"-use-full-path\",\n")
        outFile.write("        \"-here\",\n")
        outFile.write("        \"-c\",\n")
        outFile.write("      ]\n")
        outFile.write("    },\n")
        outFile.write("    \"env\": {\n")
        outFile.write("      \"PATH\": \"{}\",\n".format(os.getenv('PATH')))
        outFile.write("    },\n")
    outFile.write("    \"cwd\": \"{}\",\n".format(cwd()))
    outFile.write("  },\n")
    outFile.write("  \"tasks\": [\n")

    addBuildConfig(outFile, 0, "Bob dev", "" , buildMeFile)
    addBuildConfig(outFile, 1, "Bob dev (force)", "-f", buildMeFile)
    addBuildConfig(outFile, 2, "Bob dev (no checkout)", "-b", buildMeFile)
    addBuildConfig(outFile, 3, "Bob dev (no deps)", "-n", buildMeFile)
    addBuildConfig(outFile, 4, "Bob dev (no checkout, no deps)", "-nb", buildMeFile)
    addBuildConfig(outFile, 5, "Bob dev (no checkout, clean)", "-b --clean", buildMeFile)

    count = 6
    for name,flags in buildConfigs:
        addBuildConfig(outFile, count, name, flags, buildMeFile)
        count += 1
    
    outFile.write("  ]\n")
    outFile.write("}\n")

def compareAndRenameFileIfNotEqual(orig, new):
    if os.path.exists(orig):
        oldHash = hashFile(orig)
        newHash = hashFile(new)
        if (oldHash == newHash):
            os.remove(new)
            return
    os.rename(new, orig)

def parseArgumentLine(line):
    lines = []
    line.lstrip()
    if line.startswith('@'):
        filename = line.lstrip('@')
        try:
            lines = [line.rstrip('\n') for line in open(filename)]
        except IOError:
            raise BuildError("Input file (" + filename + ") could not be read.",
                             help="Specify the argument without leading @, if it is not a file path.")
    else:
        lines.append(line)
    return lines

def vscodeProjectGenerator(package, argv, extra, bobRoot):
    parser = argparse.ArgumentParser(prog="bob project vscode", description='Generate VS Code Project Files')
    parser.add_argument('-u', '--update', default=False, action='store_true',
                        help="Update workspace file (.code-workspace)")
    parser.add_argument('--buildCfg', action='append', default=[], type=lambda a: a.split("::"),
         help="Adds a new buildconfiguration. Format: <Name>::<flags>")
    parser.add_argument('--overwrite', action='store_true',
        help="Remove destination folder before generating.")
    parser.add_argument('--destination', metavar="DEST",
        help="Destination of project files")
    parser.add_argument('--name', metavar="NAME",
        help="Name of project. Default is complete_path_to_package")
    parser.add_argument('-I', dest="additional_includes", default=[], action='append',
        help="Additional include directories. (added recursive starting from this directory).")
    parser.add_argument('-f', '--filter', metavar="Filter",
        help="File filter. A regex for matching additional files.")
    parser.add_argument('--exclude', default=[], action='append', dest="excludes",
            help="Package filter. A regex for excluding packages in QTCreator.")
    parser.add_argument('--include', default=[], action='append', dest="include",
            help="Include package filter. A regex for including only the specified packages in QTCreator.")
    parser.add_argument('-S', dest="start_includes", default=[], action='append',
        help="Additional include directories, will be placed at the beginning of the include list.")
    parser.add_argument('-C', dest="config_defs", default=[], action='append',
        help="Add line to .config file. Can be used to specify preprocessor defines used by the QTCreator.")

    args = parser.parse_args(argv)
    extra = " ".join(quote(e) for e in extra)

    destination = args.destination
    projectName = args.name

    project = "/".join(package.getStack())

    dirs = []
    excludes = []
    try:
        if args.excludes:
            for e in args.excludes:
                excludes.append(re.compile(e))

        if args.include:
            for e in args.include:
                excludes.append(re.compile(r"^((?!"+e+").)*$"))

        # regex for all source / header files
        source  = re.compile(r"\.[ch](pp)?$")
        include = re.compile(r"\.[h](pp)?$")
        cmake = re.compile(r"\.cmake$")

        if args.filter:
           additionalFiles = re.compile(args.filter)
    except re.error as e:
        raise ParseError("Invalid regular expression '{}': {}".format(e.pattern), e)

    getCheckOutDirs(package, excludes, dirs)
    if not projectName:
        # use package name for project name
        projectName = package.getName().replace('::', '__')
    if not destination:
        # use package name for project directory
        destination = os.path.join(cwd(), "projects", projectName)
        destination = destination.replace('::', '__')
    if args.overwrite:
        removePath(destination)
    if not os.path.exists(destination):
        os.makedirs(destination)
    if not os.path.exists(os.path.join(destination, ".vscode")):
        os.makedirs(os.path.join(destination, ".vscode"))

    # Path structure:
    # destination
    #  -> files
    #  -> project dir
    #     -> symlinks

    # lists for storing all found sources files / include directories / defines
    sList = []
    hList = []
    sIncList = []
    cList = []

    sList.append("{")
    sList.append("  \"folders\": [")
    sList.append("    {")
    sList.append("      \"name\": \".\",")
    sList.append("      \"path\": \".\"")
    sList.append("    },")

    # now go through all checkout dirs to find all header / include files
    for name,path in OrderedDict(sorted(dirs, key=lambda t: t[1])).items():
        if isWindows():
            name = name.replace('::', '__')
        newPath = os.path.join(cwd(), path)

        sList.append("    {")
        sList.append("      \"name\": \"{}\",".format(name))
        sList.append("      \"path\": \"{}\"".format(newPath))
        sList.append("    },")

        for root, directories, filenames in os.walk(newPath):
            hasInclude = False
            if (((os.path.sep + '.git' + os.path.sep) in root) or
                ((os.path.sep + '.svn' + os.path.sep) in root)):
                continue
            for filename in filenames:
                if not hasInclude and include.search(filename):
                    hasInclude = True
            if hasInclude:
                oldPath = ""
                # need to recursively add all directories from the include up to cwd to get includes like
                # <a/b/c.h> resolved even if there is no include in 'a'
                relativePath = root[len(newPath):]
                for path in relativePath.split(os.path.sep):
                    includePath = os.path.join(newPath, oldPath, path)
                    if not includePath in hList:
                        hList.append(includePath)
                    oldPath = os.path.join(oldPath, path)

    for i in args.additional_includes:
        for e in parseArgumentLine(i):
            if os.path.exists(e):
                for root, directories, filenames in os.walk(e):
                    hList.append(os.path.join(e,root))

    # compose start includes
    for i in args.start_includes:
        for e in parseArgumentLine(i):
            if os.path.exists(i):
                sIncList.append(e)

    sList.append("  ],")
    sList.append("  \"settings\": {")
    sList.append("    \"C_Cpp.default.includePath\": [")
    for e in hList:
        sList.append("      \"{}\",".format(e))
    sList.append("    ],")

    # compose content of .config file (preprocessor defines)
    for i in args.config_defs:
        for e in parseArgumentLine(i):
             cList.append(e)

    sList.append("    \"C_Cpp.default.defines\": [")
    for e in cList:
        sList.append("      \"{}\",".format(e))
    sList.append("    ],")
    sList.append("  }")
    sList.append("}")

    buildMeFile = os.path.join(destination, "buildme")

    # Generate files file
    filesFile = os.path.join(destination, projectName  + ".code-workspace")
    #sList.append(buildMeFile)
    generateFile(sList, filesFile + ".new")
    compareAndRenameFileIfNotEqual(filesFile, filesFile + ".new")

    if not args.update:
        # Generate Buildme.sh
        buildMe = []
        buildMe.append("#!/bin/sh")
        buildMe.append('bob dev "$@" ' + extra + ' ' + quote(project))
        projectCmd = "bob project -n " + extra + " vscode " + quote(project) + \
            " -u --destination " + quote(destination) + ' --name ' + quote(projectName)
        # only add arguments which are relevant for .files or .includes. All other files are only modified if not build with
        # update only.
        for i in args.additional_includes:
            projectCmd += " -I " + quote(i)
        if args.filter:
            projectCmd += " --filter " + quote(args.filter)
        if args.excludes:
            for e in args.excludes:
                projectCmd += " --exclude " + quote(e)
        if args.include:
            for e in args.include:
                projectCmd += "--include " + quote(e)
        for e in args.start_includes:
            projectCmd += " -S " + quote(e)
        for e in args.config_defs:
            projectCmd += " -C " + quote(e)

        buildMe.append(projectCmd)
        generateFile(buildMe, buildMeFile)
        os.chmod(buildMeFile, stat.S_IRWXU | stat.S_IRGRP | stat.S_IWGRP |
            stat.S_IROTH | stat.S_IWOTH)

        # find all executables in package dir
        runTargets = []
        magic = summonMagic()
        if package.getPackageStep().isValid():
            packageDir = package.getPackageStep().getWorkspacePath()
            for root, directory, filenames in os.walk(packageDir):
                for filename in filenames:
                    try:
                        ftype = magic.from_file(os.path.join(root, filename))
                        if 'executable' in ftype and 'x86' in ftype and (isWindows() and "exe" in filename or not isWindows()):
                            runTargets.append(RunStep(os.path.join(cwd(), root), filename))
                    except OSError:
                        pass

        # Generate the tasks.json file using a template and modify some settings
        sharedFile = os.path.join(os.path.join(destination, ".vscode"), "tasks.json")
        with open(sharedFile, 'w') as sharedFile:
            addBuildSteps(sharedFile, buildMeFile, args.buildCfg)

        # Generate the launch.json file using a template and modify some settings
        sharedFile = os.path.join(os.path.join(destination, ".vscode"), "launch.json")
        with open(sharedFile, 'w') as sharedFile:
            addRunSteps(sharedFile, runTargets)

