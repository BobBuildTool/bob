# Bob build tool
# Copyright (C) 2019  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ..errors import BuildError, ParseError
from ..utils import removePath, isWindows, summonMagic, replacePath
import argparse
import os
import re
import sys

__all__ = ('parseArgumentLine', 'CommonIDEGenerator')

if isWindows():
    INVALID_CHAR_TRANS = str.maketrans(':*?<>"|', '_______')
else:
    INVALID_CHAR_TRANS = str.maketrans('', '')
EXCLUDE_DIRS = frozenset(['.git', '.svn', 'CVS'])
SOURCE_FILES = re.compile(r"\.[c](pp)?$", re.IGNORECASE)
HEADER_FILES = re.compile(r"\.[h](pp)?$", re.IGNORECASE)
RESOURCE_FILES = re.compile(r"\.cmake$\|^CMakeLists.txt$", re.IGNORECASE)

def filterDirs(directories):
    i = 0
    while i < len(directories):
        if directories[i] in EXCLUDE_DIRS:
            del directories[i]
        else:
            i += 1

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

class BaseScanner:
    def __init__(self, isRoot=False, stack="", additionalFiles=[]):
        self.isRoot = isRoot
        self.stack = stack
        self.workspacePath = ''
        self.__additionalFiles = additionalFiles
        self.__headers = set()
        self.__sources = set()
        self.__resources = set()
        self.__incPaths = set()
        self.__dependencies = set()
        self.__runTargets = []

    def _addFile(self, root, fileName):
        added = True

        if SOURCE_FILES.search(fileName):
            self.__sources.add(os.path.join(root, fileName))
        elif HEADER_FILES.search(fileName):
            self.__headers.add(os.path.join(root, fileName))
        elif RESOURCE_FILES.search(fileName):
            self.__resources.add(os.path.join(root, fileName))
        elif self.__additionalFiles and self.__additionalFiles.search(fileName):
            self.__resources.add(os.path.join(root, fileName))
        else:
            added = False

        return added

    def _addIncDirectory(self, directory):
        self.__incPaths.add(directory)

    def _addDepedency(self, dependency):
        self.__dependencies.add(dependency)

    def _addRunTarget(self, target):
        self.__runTargets.append(target)

    def scan(self, workspacePath):
        self.workspacePath = workspacePath
        return False

    @property
    def headers(self):
        return sorted(self.__headers)

    @property
    def sources(self):
        return sorted(self.__sources)

    @property
    def resources(self):
        return sorted(self.__resources)

    @property
    def incPaths(self):
        return sorted(self.__incPaths)

    @property
    def dependencies(self):
        return sorted(self.__dependencies)

    @property
    def runTargets(self):
        return self.__runTargets


class GenericScanner(BaseScanner):
    def __init__(self, isRoot, stack, additionalFiles):
        super().__init__(isRoot, stack, additionalFiles)

    def scan(self, workspacePath):
        super().scan(workspacePath)
        ret = False

        for root, directories, filenames in os.walk(workspacePath):
            # remove scm directories, os.walk will only descend into directiries
            # that stay in the list
            filterDirs(directories)
            hasInclude = False
            for filename in filenames:
                ret = self._addFile(root, filename) or ret
                hasInclude = hasInclude or HEADER_FILES.search(filename)
            if hasInclude:
                oldPath = ""
                # need to recursively add all directories from the include up to cwd to get includes like
                # <a/b/c.h> resolved even if there is no include in 'a'
                relativePath = root[len(workspacePath):]
                for path in relativePath.split(os.path.sep):
                    includePath = os.path.join(workspacePath, oldPath, path)
                    self._addIncDirectory(includePath)
                    oldPath = os.path.join(oldPath, path)

        return ret

SCANNERS = [
    GenericScanner  # must be last as "catch all" last resort
]

class CommonIDEGenerator:

    def __init__(self, prog, description):
        parser = argparse.ArgumentParser(prog="bob project " + prog,
                                         description=description)
        self.parser = parser

        parser.add_argument('--name', metavar="NAME",
            help="Name of project (default: package name).")
        parser.add_argument('--destination', metavar="DEST",
            help="Destination of project files (default: projects/NAME).")
        parser.add_argument('--overwrite', action='store_true',
            help="Remove destination folder before generating.")
        parser.add_argument('-u', '--update', default=False, action='store_true',
            help="Update project files")

        parser.add_argument('--exclude', default=[], action='append', dest="excludes",
                help="Package filter. A regex for excluding packages in QTCreator.")
        parser.add_argument('--include', default=[], action='append', dest="include",
                help="Include package filter. A regex for including only the specified packages in QTCreator.")

        parser.add_argument('-I', dest="additional_includes", default=[], action='append',
            help="Additional include directories. (added recursive starting from this directory).")
        parser.add_argument('-S', dest="start_includes", default=[], action='append',
            help="Additional include directories, will be placed at the beginning of the include list.")
        parser.add_argument('-f', '--filter', metavar="Filter",
            help="File filter. A regex for matching additional files.")

    def __walk(self, package, excludes, additionalFiles):
        class CheckoutInfo:
            def __init__(self, scan, packageVid):
                self.scan = scan
                self.packages = set([packageVid])
                self.name = None

        class PackageInfo:
            def __init__(self, recipeName, packageName):
                self.recipeName = recipeName
                self.packageName = packageName
                self.checkout = False
                self.dependencies = set()

        checkouts = { False : CheckoutInfo(BaseScanner(), None) }
        packages = {}

        def collect(package, rootPackage=True):
            # only once per package
            packageVid = package.getPackageStep().getVariantId()
            if packageVid in packages: return
            packages[packageVid] = packageInfo = PackageInfo(package.getRecipe().getName(), package.getName())

            # only scan if checkout step is valid and was not already scanned
            checkoutPath = package.getCheckoutStep().isValid() and \
                package.getCheckoutStep().getWorkspacePath()
            if checkoutPath not in checkouts:
                # take result from first scanner that succeeds
                for scanner in SCANNERS:
                    scan = scanner(rootPackage, "/".join(package.getStack()), additionalFiles)
                    if scan.scan(checkoutPath):
                        break
                else:
                    scan = BaseScanner()
                info = checkouts[checkoutPath] = CheckoutInfo(scan, packageVid)
            elif rootPackage:
                # make sure that root package has at least an empty checkout to be always visible
                checkoutPath = "<root-sentinel>"
                info = checkouts[checkoutPath] = CheckoutInfo(BaseScanner(True, "/".join(package.getStack())), packageVid)
            else:
                info = checkouts[checkoutPath]
                info.packages.add(packageVid)

            # find all executables
            if package.getPackageStep().isValid():
                packageDir = package.getPackageStep().getWorkspacePath()
                if isWindows():
                    for root, directory, filenames in os.walk(packageDir):
                        for filename in filenames:
                            target = os.path.join(root, filename)
                            if target.lower().endswith(".exe"):
                                info.scan._addRunTarget(target)
                else:
                    magic = summonMagic()
                    for root, directory, filenames in os.walk(packageDir):
                        for filename in filenames:
                            target = os.path.join(root, filename)
                            try:
                                ftype = magic.from_file(target)
                                if 'executable' in ftype and 'x86' in ftype:
                                    info.scan._addRunTarget(target)
                            except OSError:
                                pass

            # descend on used dependencies
            packageInfo.checkout = checkoutPath
            for d in package.getBuildStep().getArguments():
                depName = d.getPackage().getName()
                if any(e.search(depName) for e in excludes):
                    continue
                if d.getPackage() == package:
                    continue
                packageInfo.dependencies.add(d.getVariantId())
                collect(d.getPackage(), False)

        # Collect all packages and their respective checkouts
        collect(package)
        del checkouts[False] # remove sentinel

        # Loop through all checkouts and assign a name. If there is a single
        # package using it then we take the package name. Otherwise we take the
        # recipe name. If multiple checkouts have the same name we add a
        # counting suffix.
        names = set()
        for checkoutPath, info in sorted(checkouts.items()):
            checkoutPackages = [ packages[vid] for vid in sorted(info.packages) ]
            if len(checkoutPackages) > 1:
                # pick the first name if multiple recipes refer to the same checkout
                name = sorted(set(p.recipeName for p in checkoutPackages))[0]
            else:
                name = checkoutPackages[0].packageName

            name = name.translate(INVALID_CHAR_TRANS)
            suffix = ""
            num = 1
            while name+suffix in names:
                suffix = "-{}".format(num)
                num += 1
            name += suffix
            names.add(name)
            info.name = name

        # Walk all packages
        # FIXME: prevent cyclic dependencies
        result = {}
        done = set()
        todo = [ package.getPackageStep().getVariantId() ]
        while todo:
            vid = todo.pop(0)
            if vid in done: continue
            info = packages[vid]
            done.add(vid)

            co = checkouts.get(info.checkout)
            if co:
                result[co.name] = co.scan
                for dep in info.dependencies:
                    depCo = checkouts.get(packages[dep].checkout)
                    if depCo is not None: co.scan._addDepedency(depCo.name)

            todo.extend(d for d in sorted(info.dependencies) if d not in done)

        return result

    def configure(self, package, argv):
        self.rootPackage = package
        self.args  = self.parser.parse_args(argv)

        self.projectName = (self.args.name or package.getName()).translate(INVALID_CHAR_TRANS)
        self.destination = (self.args.destination or
            os.path.join("projects", self.projectName)).translate(INVALID_CHAR_TRANS)

        excludes = []
        try:
            for e in self.args.excludes: excludes.append(re.compile(e))
            for e in self.args.include: excludes.append(re.compile(r"^((?!"+e+").)*$"))
            additionalFiles = re.compile(self.args.filter) if self.args.filter else None
        except re.error as e:
            raise ParseError("Invalid regular expression '{}': {}".format(e.pattern, e))

        self.packages = self.__walk(package, excludes, additionalFiles)

        # include directories that are statically prepended
        self.prependIncludeDirectories = []
        for i in self.args.start_includes:
            for e in parseArgumentLine(i):
                if os.path.exists(e):
                    self.prependIncludeDirectories.append(e)

        # include directories that are statically appended
        self.appendIncludeDirectories = []
        for i in self.args.additional_includes:
            for e in parseArgumentLine(i):
                if os.path.exists(e):
                    self.appendIncludeDirectories.append(e)

    def generate(self):
        if self.args.overwrite:
            removePath(self.destination)
        if not os.path.exists(self.destination):
            os.makedirs(self.destination)

    def updateFile(self, name, content, encoding=None, newline=None):
        newName = name+".new"
        oldName = name+".old"
        with open(newName, "w", encoding=encoding, newline=newline) as f:
            f.write(content)
        with open(newName, "rb") as f:
            newContent = f.read()
        try:
            with open(oldName, "rb") as f:
                oldContent = f.read()
        except OSError:
            oldContent = None

        if oldContent != newContent:
            replacePath(newName, name)
            with open(oldName, "wb") as f:
                f.write(newContent)
        else:
            os.remove(newName)
