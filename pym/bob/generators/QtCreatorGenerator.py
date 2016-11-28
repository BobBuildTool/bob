# Bob build tool
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

# Generator for QT-creator project files

import argparse
import sys
import re
import os
import shutil
import stat
import xml.etree.ElementTree
from os.path import expanduser
from os.path import join
from bob.utils import removePath
from bob.errors import BuildError
from bob.utils import summonMagic, hashFile
from collections import OrderedDict, namedtuple
from bob.tty import colorize
from pipes import quote

# scan package recursivelely with its dependencies and build a list of checkout dirs
def getCheckOutDirs(package, dirs):
    if package.getCheckoutStep().isValid():
        dirs.append([package.getName(), package.getCheckoutStep().getWorkspacePath()])
    for d in package.getDirectDepSteps():
        getCheckOutDirs(d.getPackage(), dirs)

def generateFile(entries, fileName):
    try:
        os.remove(fileName)
    except OSError:
        pass
    fileName = open(fileName, "w")
    for e in entries:
        fileName.write(e + "\n")
    fileName.close()

def addRunStep(outFile, num, cmd, directory):
   outFile.write('<valuemap type="QVariantMap" key="ProjectExplorer.Target.RunConfiguration.' + str(num) + '">')
   outFile.write(' <value type="QString" key="ProjectExplorer.CustomExecutableRunConfiguration.Arguments"></value>')
   outFile.write(' <value type="QString" key="ProjectExplorer.CustomExecutableRunConfiguration.Executable">' + cmd + '</value>')
   outFile.write(' <value type="QString" key="ProjectExplorer.CustomExecutableRunConfiguration.WorkingDirectory">' + directory + '</value>')
   outFile.write(' <value type="QString" key="ProjectExplorer.ProjectConfiguration.DefaultDisplayName">' + cmd + '</value>')
   outFile.write(' <value type="QString" key="ProjectExplorer.ProjectConfiguration.DisplayName"></value>')
   outFile.write(' <value type="QString" key="ProjectExplorer.ProjectConfiguration.Id">ProjectExplorer.CustomExecutableRunConfiguration</value>')
   outFile.write(' <value type="bool" key="RunConfiguration.UseCppDebugger">false</value>')
   outFile.write(' <value type="bool" key="RunConfiguration.UseCppDebuggerAuto">true</value>')
   outFile.write(' <value type="bool" key="RunConfiguration.UseMultiProcess">false</value>')
   outFile.write(' <value type="bool" key="RunConfiguration.UseQmlDebugger">false</value>')
   outFile.write(' <value type="bool" key="RunConfiguration.UseQmlDebuggerAuto">true</value>')
   outFile.write('</valuemap>')

RunStep = namedtuple("RunStep", "dir cmd")

def addRunSteps(outFile, runTargets):
    runTargetCnt = 0
    for directory,cmd in runTargets:
        addRunStep(outFile, runTargetCnt, cmd, directory)
        runTargetCnt += 1
    outFile.write('<value type="int" key="ProjectExplorer.Target.RunConfigurationCount">' + str(runTargetCnt) + '</value>')

def addBuildConfig(outFile, num, name, buildArgs, buildMeFile):
    outFile.write('<valuemap type="QVariantMap" key="ProjectExplorer.Target.BuildConfiguration.' + str(num) + '">\n')
    outFile.write('<value type="QString" key="ProjectExplorer.BuildConfiguration.BuildDirectory">' + os.getcwd() + '</value>\n')
    outFile.write('<valuemap type="QVariantMap" key="ProjectExplorer.BuildConfiguration.BuildStepList.0">\n')
    outFile.write(' <valuemap type="QVariantMap" key="ProjectExplorer.BuildStepList.Step.0">\n')
    outFile.write('  <value type="bool" key="ProjectExplorer.BuildStep.Enabled">true</value>\n')
    outFile.write('  <value type="QString" key="ProjectExplorer.ProcessStep.Arguments">' + buildArgs + '</value>\n')
    outFile.write('  <value type="QString" key="ProjectExplorer.ProcessStep.Command">' + buildMeFile + '</value>\n')
    outFile.write('  <value type="QString" key="ProjectExplorer.ProcessStep.WorkingDirectory">%{buildDir}</value>\n')
    outFile.write('  <value type="QString" key="ProjectExplorer.ProjectConfiguration.DefaultDisplayName">' + name + '</value>\n')
    outFile.write('  <value type="QString" key="ProjectExplorer.ProjectConfiguration.DisplayName"></value>\n')
    outFile.write('  <value type="QString" key="ProjectExplorer.ProjectConfiguration.Id">ProjectExplorer.ProcessStep</value>\n')
    outFile.write(' </valuemap>\n')
    outFile.write(' <value type="int" key="ProjectExplorer.BuildStepList.StepsCount">1</value>\n')
    outFile.write(' <value type="QString" key="ProjectExplorer.ProjectConfiguration.DefaultDisplayName">Build</value>\n')
    outFile.write(' <value type="QString" key="ProjectExplorer.ProjectConfiguration.DisplayName"></value>\n')
    outFile.write(' <value type="QString" key="ProjectExplorer.ProjectConfiguration.Id">ProjectExplorer.BuildSteps.Build</value>\n')
    outFile.write('</valuemap>\n')
    outFile.write('<value type="int" key="ProjectExplorer.BuildConfiguration.BuildStepListCount">1</value>\n')
    outFile.write('<value type="bool" key="ProjectExplorer.BuildConfiguration.ClearSystemEnvironment">false</value>\n')
    outFile.write('<valuelist type="QVariantList" key="ProjectExplorer.BuildConfiguration.UserEnvironmentChanges">\n')
    outFile.write(' <value type="QString">' + "PATH=" + os.environ['PATH'] + '  </value>\n')
    outFile.write('</valuelist>\n')
    outFile.write('<value type="QString" key="ProjectExplorer.ProjectConfiguration.DefaultDisplayName">Vorgabe</value>\n')
    outFile.write('<value type="QString" key="ProjectExplorer.ProjectConfiguration.DisplayName">' + name + '</value>\n')
    outFile.write('<value type="QString" key="ProjectExplorer.ProjectConfiguration.Id">GenericProjectManager.GenericBuildConfiguration</value>\n')
    outFile.write('<valuelist type="QVariantList" key="UserStickyKeys">\n')
    outFile.write(' <value type="QString">ProjectExplorer.BuildConfiguration.UserEnvironmentChanges</value>\n')
    outFile.write(' <value type="QString">UserStickyKeys</value>\n')
    outFile.write('</valuelist>\n')
    outFile.write('</valuemap>\n')

def addBuildSteps(outFile, buildMeFile, buildConfigs):
    addBuildConfig(outFile, 0, "Bob dev ", "" , buildMeFile)
    addBuildConfig(outFile, 1, "Bob dev (force)", "-f", buildMeFile)
    addBuildConfig(outFile, 2, "Bob dev (no checkout)", "-b", buildMeFile)
    addBuildConfig(outFile, 3, "Bob dev (no deps)", "-n", buildMeFile)
    addBuildConfig(outFile, 4, "Bob dev (no checkout, no deps)", "-nb", buildMeFile)
    addBuildConfig(outFile, 5, "Bob dev (no checkout, clean)", "-b --clean", buildMeFile)

    count = 6
    for name,flags in buildConfigs:
        addBuildConfig(outFile, count, name, flags, buildMeFile)
        count += 1

    outFile.write('<value type="int" key="ProjectExplorer.Target.BuildConfigurationCount">' + str(count) + '</value>\n')
    outFile.write('<valuelist type="QVariantList" key="UserStickyKeys">\n')
    outFile.write(' <value type="QString">ProjectExplorer.BuildConfiguration.BuildStepListCount</value>\n')
    outFile.write(' <value type="QString">ProjectExplorer.BuildConfiguration.UserEnvironmentChanges</value>\n')
    outFile.write(' <value type="QString">ProjectExplorer.ProjectConfiguration.DisplayName</value>\n')
    outFile.write('</valuelist>\n')

def compareAndRenameFileIfNotEqual(orig, new):
    if os.path.exists(orig):
        oldHash = hashFile(orig)
        newHash = hashFile(new)
        if (oldHash == newHash):
            os.remove(new)
            return
    os.rename(new, orig)

def qtProjectGenerator(package, argv, extra):
    parser = argparse.ArgumentParser(prog="bob project qt-project", description='Generate QTCreator Project Files')
    parser.add_argument('-u', '--update', default=False, action='store_true',
                        help="Update project files (.files, .includes)")
    parser.add_argument('--buildCfg', action='append', default=[], type=lambda a: a.split("::"),
         help="Adds a new buildconfiguration. Format: <Name>::<flags>")
    parser.add_argument('--overwrite', action='store_true',
        help="Remove destination folder before generating.")
    parser.add_argument('--destination', metavar="DEST",
        help="Destination of project files")
    parser.add_argument('--name', metavar="NAME",
        help="Name of project. Default is complete_path_to_package")
    parser.add_argument('-I', dest="additional_includes", default=[], action='append',
        help="Additional include directories. (added recursive starting from this directory)")
    parser.add_argument('-f', '--filter', metavar="Filter",
        help="File filter. A regex for matching additional files.")
    parser.add_argument('--kit',
        help="Kit to use for this project")

    args = parser.parse_args(argv)
    extra = " ".join(quote(e) for e in extra)

    destination = args.destination
    projectName = args.name

    project = "/".join(package.getStack())

    dirs = []
    getCheckOutDirs(package, dirs)
    if not projectName:
        # use package name for project name
        projectName = package.getName()
    if not destination:
        # use package stack for project directory
        destination = os.path.join(os.getcwd(), "projects", "_".join(package.getStack()))
        destination = destination.replace(':', '_')
    if args.overwrite:
        removePath(destination)
    if not os.path.exists(destination):
        os.makedirs(destination)

    # Path structure:
    # destination
    #  -> files
    #  -> project dir
    #     -> symlinks

    # create a 'flat' source tree
    symlinkDir = os.path.join(destination, projectName)
    if not args.update:
       if os.path.exists(symlinkDir):
          shutil.rmtree(symlinkDir)
       os.makedirs(symlinkDir)

    # regex for all source / header files
    source  = re.compile(r".*\.[ch](pp)?$")
    include = re.compile(r".*\.[h](pp)?$")
    cmake = re.compile(r".*\.cmake$")

    if args.filter:
       additionalFiles = re.compile(args.filter)

    # lists for storing all found sources files / include directories
    sList = []
    hList = []

    # now go through all checkout dirs to find all header / include files
    for name,path in OrderedDict(sorted(dirs, key=lambda t: t[1])).items():
        newPath = os.path.join(symlinkDir, name)
        if not args.update: os.symlink(os.path.join(os.getcwd(), path), newPath)
        for root, directories, filenames in os.walk(newPath):
            hasInclude = False
            if (((os.path.sep + '.git' + os.path.sep) in root) or
                ((os.path.sep + '.svn' + os.path.sep) in root)):
                continue
            for filename in filenames:
                if source.match(filename) or cmake.match(filename) or filename == 'CMakeLists.txt':
                    sList.append(os.path.join(os.getcwd(), os.path.join(root,filename)))
                if args.filter and additionalFiles.match(filename):
                    sList.append(os.path.join(os.getcwd(), os.path.join(root,filename)))
                if not hasInclude and include.match(filename):
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
        if os.path.exists(i):
            for root, directories, filenames in os.walk(i):
                hList.append(os.path.join(i,root))

    # use default kit "Desktop" if no kit is given
    if args.kit is None:
        _kit = re.compile(r".*Desktop.*")
    else:
        _kit = re.compile(r""+args.kit)

    id = None
    name = None
    kits = []
    try:
        profiles = xml.etree.ElementTree.parse(os.path.join(expanduser('~'), ".config/QtProject/qtcreator/profiles.xml")).getroot()
        for profile in profiles:
            for valuemap in profile:
                id = None
                name = None
                for value in valuemap.findall('value'):
                    if (value.attrib.get('key') == 'PE.Profile.Id'):
                        id = str(value.text)
                    if (value.attrib.get('key') == 'PE.Profile.Name'):
                        name = str(value.text)
                    if (id is not None) and (name is not None) and (_kit.match(name)):
                        kits.append([name, id])
                        break
    except FileNotFoundError:
        # if kits are generared using sdk tool they are stored somewhere else... (/usr/share...)
        pass

    if (len(kits) == 0):
        if (args.kit is None):
            raise BuildError("No kit found!",
                help = "Run again with '--kit' and specify a kit or generate a Desktop kit, which is used by default.")
        kitName = args.kit
        kitId = args.kit
    else:
        if (len(kits) > 1):
            print(colorize("Warning: {} kits found. Using '{}'.".format(len(kits), str(kits[0][0])), "33"))
        kitName = kits[0][0]
        kitId = kits[0][1]

    buildMeFile = os.path.join(destination, "buildme")

    # Generate the includes file. Create a temporary file first and compare it with the old one.
    # only use the new one if different to prevent reindexing after each build.
    includesFile = os.path.join(destination, projectName + ".includes")
    generateFile(sorted(hList, key=lambda file: (os.path.dirname(file))), includesFile + ".new")
    compareAndRenameFileIfNotEqual(includesFile, includesFile + ".new")

    # Generate files file
    filesFile = os.path.join(destination, projectName  + ".files")
    sList.append(buildMeFile)
    generateFile(sorted(sList, key=lambda file: (os.path.dirname(file), os.path.basename(file))), filesFile + ".new")
    compareAndRenameFileIfNotEqual(filesFile, filesFile + ".new")


    if not args.update:
        # Generate Buildme.sh
        buildMe = []
        buildMe.append("#!/bin/sh")
        buildMe.append('bob dev "$@" ' + extra + ' ' + quote(project))
        projectCmd = "bob project -n qt-creator " + quote(project) + " -u --destination " + quote(destination) + ' --name ' + quote(projectName)
        # only add arguments which are relevant for .files or .includes. All other files are only modified if not build with
        # update only.
        for i in args.additional_includes:
            projectCmd += " -I " + quote(i)
        if args.filter:
            projectCmd += " --filter " + quote(args.filter)

        buildMe.append(projectCmd)
        generateFile(buildMe, buildMeFile)
        os.chmod(buildMeFile, stat.S_IRWXU | stat.S_IRGRP | stat.S_IWGRP |
            stat.S_IROTH | stat.S_IWOTH)
        # Generate a .config file
        generateFile("", os.path.join(destination, projectName  + ".config"))
        # Generate creator file
        creatorFile = os.path.join(destination, projectName  + ".creator")
        generateFile([], creatorFile)
        # Generate the creator.shared file using a template and modify some settings
        sharedFile = os.path.join(destination, projectName  + ".creator.shared")

        template_head = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE QtCreatorProject>
<qtcreator>
 <data>
  <variable>ProjectExplorer.Project.ActiveTarget</variable>
  <value type="int">0</value>
 </data>
 <data>
  <variable>ProjectExplorer.Project.EditorSettings</variable>
  <valuemap type="QVariantMap">
   <value type="bool" key="EditorConfiguration.AutoIndent">true</value>
   <value type="bool" key="EditorConfiguration.AutoSpacesForTabs">false</value>
   <value type="bool" key="EditorConfiguration.CamelCaseNavigation">true</value>
   <valuemap type="QVariantMap" key="EditorConfiguration.CodeStyle.0">
    <value type="QString" key="language">Cpp</value>
    <valuemap type="QVariantMap" key="value">
     <value type="QByteArray" key="CurrentPreferences">CppGlobal</value>
    </valuemap>
   </valuemap>
   <valuemap type="QVariantMap" key="EditorConfiguration.CodeStyle.1">
    <value type="QString" key="language">QmlJS</value>
    <valuemap type="QVariantMap" key="value">
     <value type="QByteArray" key="CurrentPreferences">QmlJSGlobal</value>
    </valuemap>
   </valuemap>
   <value type="int" key="EditorConfiguration.CodeStyle.Count">2</value>
   <value type="QByteArray" key="EditorConfiguration.Codec">UTF-8</value>
   <value type="bool" key="EditorConfiguration.ConstrainTooltips">false</value>
   <value type="int" key="EditorConfiguration.IndentSize">4</value>
   <value type="bool" key="EditorConfiguration.KeyboardTooltips">false</value>
   <value type="int" key="EditorConfiguration.MarginColumn">80</value>
   <value type="bool" key="EditorConfiguration.MouseHiding">true</value>
   <value type="bool" key="EditorConfiguration.MouseNavigation">true</value>
   <value type="int" key="EditorConfiguration.PaddingMode">1</value>
   <value type="bool" key="EditorConfiguration.ScrollWheelZooming">true</value>
   <value type="bool" key="EditorConfiguration.ShowMargin">false</value>
   <value type="int" key="EditorConfiguration.SmartBackspaceBehavior">0</value>
   <value type="bool" key="EditorConfiguration.SmartSelectionChanging">true</value>
   <value type="bool" key="EditorConfiguration.SpacesForTabs">true</value>
   <value type="int" key="EditorConfiguration.TabKeyBehavior">0</value>
   <value type="int" key="EditorConfiguration.TabSize">4</value>
   <value type="bool" key="EditorConfiguration.UseGlobal">true</value>
   <value type="int" key="EditorConfiguration.Utf8BomBehavior">1</value>
   <value type="bool" key="EditorConfiguration.addFinalNewLine">true</value>
   <value type="bool" key="EditorConfiguration.cleanIndentation">true</value>
   <value type="bool" key="EditorConfiguration.cleanWhitespace">true</value>
   <value type="bool" key="EditorConfiguration.inEntireDocument">false</value>
  </valuemap>
 </data>
 <data>
  <variable>ProjectExplorer.Project.PluginSettings</variable>
  <valuemap type="QVariantMap"/>
 </data>"""

        template_foot = """
 <data>
  <variable>ProjectExplorer.Project.TargetCount</variable>
  <value type="int">1</value>
 </data>
 <data>
  <variable>ProjectExplorer.Project.Updater.FileVersion</variable>
  <value type="int">18</value>
 </data>
 <data>
  <variable>Version</variable>
  <value type="int">18</value>
 </data>
</qtcreator>"""

        # find all executables in package dir
        runTargets = []
        magic = summonMagic()
        if package.getPackageStep().isValid():
            packageDir = package.getPackageStep().getWorkspacePath()
            for root, directory, filenames in os.walk(packageDir):
                for filename in filenames:
                    ftype = magic.from_file(os.path.join(root, filename))
                    if 'executable' in ftype and 'x86' in ftype:
                        runTargets.append(RunStep(os.path.join(os.getcwd(), root), filename))

        with open(sharedFile, 'w') as sharedFile:
            sharedFile.write(template_head)
            sharedFile.write(' <data>\n')
            sharedFile.write('  <variable>ProjectExplorer.Project.Target.0</variable>\n')
            sharedFile.write('  <valuemap type="QVariantMap">\n')
            sharedFile.write('   <value type="QString" key="ProjectExplorer.ProjectConfiguration.DefaultDisplayName">' + kitName + '</value>\n')
            sharedFile.write('   <value type="QString" key="ProjectExplorer.ProjectConfiguration.DisplayName">' + kitName + '</value>\n')
            sharedFile.write('   <value type="QString" key="ProjectExplorer.ProjectConfiguration.Id">' + kitId + '</value>\n')
            sharedFile.write('   <value type="int" key="ProjectExplorer.Target.ActiveBuildConfiguration">0</value>\n')
            sharedFile.write('   <value type="int" key="ProjectExplorer.Target.ActiveDeployConfiguration">0</value>\n')
            sharedFile.write('   <value type="int" key="ProjectExplorer.Target.ActiveRunConfiguration">0</value>\n')
            addBuildSteps(sharedFile, buildMeFile, args.buildCfg)
            sharedFile.write('   <value type="int" key="ProjectExplorer.Target.DeployConfigurationCount">0</value>\n')
            sharedFile.write('   <valuemap type="QVariantMap" key="ProjectExplorer.Target.PluginSettings"/>\n')
            addRunSteps(sharedFile, runTargets)
            sharedFile.write('  </valuemap>\n')
            sharedFile.write(' </data>\n')
            sharedFile.write(template_foot)


