# Bob build tool
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Generator for eclipse cdt project files

import argparse
import sys
import os
import re
import shutil
import stat
import uuid
from os.path import expanduser
from os.path import join
from bob.utils import summonMagic, removePath
from bob.errors import ParseError
from collections import OrderedDict
from shlex import quote

# scan package recursivelely with its dependencies and build a list of checkout dirs
def getCheckOutDirs(package, dirs):
    def collect(package, dirs, steps, processed):
        if package._getId() in processed:
            return
        processed.add(package._getId())
        if package.getCheckoutStep().isValid():
            if package.getCheckoutStep().getVariantId() not in steps:
                steps.add(package.getCheckoutStep().getVariantId())
                dirs.append([package.getName(), package.getCheckoutStep().getWorkspacePath()])
        for d in package.getDirectDepSteps():
            collect(d.getPackage(), dirs, steps, processed)

    collect(package, dirs, set(), set())

# generate a unique id
def getId():
    _uuid = uuid.uuid4()
    return str(int(_uuid.hex[0:7], 16))

def generateFile(entries, fileName):
    try:
        os.remove(fileName)
    except OSError:
        pass
    fileName = open(fileName, "w")
    for e in entries:
        fileName.write(e + "\n")
    fileName.close()

def createLaunchFile(outFile, exe, project):
    outFile.write('<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n')
    outFile.write('<launchConfiguration type="org.eclipse.cdt.launch.applicationLaunchType">\n')
    outFile.write('<booleanAttribute key="org.eclipse.cdt.dsf.gdb.AUTO_SOLIB" value="true"/>\n')
    outFile.write('<listAttribute key="org.eclipse.cdt.dsf.gdb.AUTO_SOLIB_LIST"/>\n')
    outFile.write('<stringAttribute key="org.eclipse.cdt.dsf.gdb.DEBUG_NAME" value="gdb"/>\n')
    outFile.write('<booleanAttribute key="org.eclipse.cdt.dsf.gdb.DEBUG_ON_FORK" value="false"/>\n')
    outFile.write('<stringAttribute key="org.eclipse.cdt.dsf.gdb.GDB_INIT" value=".gdbinit"/>\n')
    outFile.write('<booleanAttribute key="org.eclipse.cdt.dsf.gdb.NON_STOP" value="false"/>\n')
    outFile.write('<booleanAttribute key="org.eclipse.cdt.dsf.gdb.REVERSE" value="false"/>\n')
    outFile.write('<listAttribute key="org.eclipse.cdt.dsf.gdb.SOLIB_PATH"/>\n')
    outFile.write('<stringAttribute key="org.eclipse.cdt.dsf.gdb.TRACEPOINT_MODE" value="TP_NORMAL_ONLY"/>\n')
    outFile.write('<booleanAttribute key="org.eclipse.cdt.dsf.gdb.UPDATE_THREADLIST_ON_SUSPEND" value="false"/>\n')
    outFile.write('<booleanAttribute key="org.eclipse.cdt.dsf.gdb.internal.ui.launching.LocalApplicationCDebuggerTab.DEFAULTS_SET" value="true"/>\n')
    outFile.write('<intAttribute key="org.eclipse.cdt.launch.ATTR_BUILD_BEFORE_LAUNCH_ATTR" value="2"/>\n')
    outFile.write('<stringAttribute key="org.eclipse.cdt.launch.COREFILE_PATH" value=""/>\n')
    outFile.write('<stringAttribute key="org.eclipse.cdt.launch.DEBUGGER_ID" value="gdb"/>\n')
    outFile.write('<stringAttribute key="org.eclipse.cdt.launch.DEBUGGER_REGISTER_GROUPS" value=""/>\n')
    outFile.write('<stringAttribute key="org.eclipse.cdt.launch.DEBUGGER_START_MODE" value="run"/>\n')
    outFile.write('<booleanAttribute key="org.eclipse.cdt.launch.DEBUGGER_STOP_AT_MAIN" value="true"/>\n')
    outFile.write('<stringAttribute key="org.eclipse.cdt.launch.DEBUGGER_STOP_AT_MAIN_SYMBOL" value="main"/>\n')
    outFile.write('<stringAttribute key="org.eclipse.cdt.launch.PROGRAM_NAME" value="'+ exe + '"/>\n')
    outFile.write('<stringAttribute key="org.eclipse.cdt.launch.PROJECT_ATTR" value="' + project + '"/>\n')
    outFile.write('<booleanAttribute key="org.eclipse.cdt.launch.PROJECT_BUILD_CONFIG_AUTO_ATTR" value="true"/>\n')
    outFile.write('<stringAttribute key="org.eclipse.cdt.launch.PROJECT_BUILD_CONFIG_ID_ATTR" value=""/>\n')
    outFile.write('<listAttribute key="org.eclipse.debug.core.MAPPED_RESOURCE_PATHS">\n')
    outFile.write('<listEntry value="/' + project + '"/>\n')
    outFile.write('</listAttribute>\n')
    outFile.write('<listAttribute key="org.eclipse.debug.core.MAPPED_RESOURCE_TYPES">\n')
    outFile.write('<listEntry value="4"/>\n')
    outFile.write('</listAttribute>\n')
    outFile.write('<listAttribute key="org.eclipse.debug.ui.favoriteGroups">\n')
    outFile.write('<listEntry value="org.eclipse.debug.ui.launchGroup.debug"/>\n')
    outFile.write('<listEntry value="org.eclipse.debug.ui.launchGroup.run"/>\n')
    outFile.write('</listAttribute>\n')
    outFile.write('<stringAttribute key="org.eclipse.dsf.launch.MEMORY_BLOCKS" value="&lt;?xml version=&quot;1.0&quot; encoding=&quot;UTF-8&quot; standalone=&quot;no&quot;?&gt;&#10;&lt;memoryBlockExpressionList context=&quot;reserved-for-future-use&quot;/&gt;&#10;"/>\n')
    outFile.write('<stringAttribute key="process_factory_id" value="org.eclipse.cdt.dsf.gdb.GdbProcessFactory"/>\n')
    outFile.write('</launchConfiguration>')

def addIncludes(cProjectFile, includeDirs):
    cProjectFile.write('<option id="org.eclipse.cdt.build.core.settings.holder.incpaths.'+getId()+'" name="Include Paths" superClass="org.eclipse.cdt.build.core.settings.holder.incpaths" valueType="includePath">\n')
    for i in includeDirs:
        cProjectFile.write(' <listOptionValue builtIn="false" value="'+i+'"/>\n')
    cProjectFile.write('</option>\n')

def addCConfig(cProjectFile, excludePackages, includeDirs, buildName, id, buildArgs, buildMeFile):
    cProjectFile.write('<cconfiguration id="' + id + '">\n')
    cProjectFile.write('<storageModule buildSystemId="org.eclipse.cdt.managedbuilder.core.configurationDataProvider" id="'
                        + id + '" moduleId="org.eclipse.cdt.core.settings" name="' + buildName + '">\n')
    cProjectFile.write(' <externalSettings/>\n')
    cProjectFile.write(' <extensions>\n')
    cProjectFile.write('  <extension id="org.eclipse.cdt.core.GASErrorParser" point="org.eclipse.cdt.core.ErrorParser"/>\n')
    cProjectFile.write('  <extension id="org.eclipse.cdt.core.GmakeErrorParser" point="org.eclipse.cdt.core.ErrorParser"/>\n')
    cProjectFile.write('  <extension id="org.eclipse.cdt.core.GLDErrorParser" point="org.eclipse.cdt.core.ErrorParser"/>\n')
    cProjectFile.write('  <extension id="org.eclipse.cdt.core.VCErrorParser" point="org.eclipse.cdt.core.ErrorParser"/>\n')
    cProjectFile.write('  <extension id="org.eclipse.cdt.core.CWDLocator" point="org.eclipse.cdt.core.ErrorParser"/>\n')
    cProjectFile.write('  <extension id="org.eclipse.cdt.core.GCCErrorParser" point="org.eclipse.cdt.core.ErrorParser"/>\n')
    cProjectFile.write(' </extensions>\n')
    cProjectFile.write('</storageModule>\n')
    cProjectFile.write('<storageModule moduleId="cdtBuildSystem" version="4.0.0">\n')
    cProjectFile.write(' <configuration buildProperties="" description="" id="' + id + '" name="' + buildName + '" parent="org.eclipse.cdt.build.core.prefbase.cfg">\n')
    cProjectFile.write('   <folderInfo id="' + id + '." name="/" resourcePath="">\n')
    toolchainId = getId()
    cProjectFile.write('    <toolChain id="org.eclipse.cdt.build.core.prefbase.toolchain.' + toolchainId + '" name="No ToolChain" resourceTypeBasedDiscovery="false" superClass="org.eclipse.cdt.build.core.prefbase.toolchain">\n')
    cProjectFile.write('     <targetPlatform id="org.eclipse.cdt.build.core.prefbase.toolchain.' + toolchainId + '.' + getId() + '" name=""/>\n')
    cProjectFile.write('     <builder command="' + buildMeFile + '" id="org.eclipse.cdt.build.core.settings.default.builder.' + getId()
            + '" incrementalBuildTarget="' + buildArgs
            + '" buildPath="' + os.getcwd()
            + '" keepEnvironmentInBuildfile="false" managedBuildOn="false" name="Gnu Make Builder" \
                    superClass="org.eclipse.cdt.build.core.settings.default.builder.' + getId() + '"/>\n')
    cProjectFile.write('     <tool id="org.eclipse.cdt.build.core.settings.holder.libs.' + getId() + '" name="holder for library settings" superClass="org.eclipse.cdt.build.core.settings.holder.libs"/>\n')
    cProjectFile.write('     <tool id="org.eclipse.cdt.build.core.settings.holder.' + getId() + '" name="Assembly" superClass="org.eclipse.cdt.build.core.settings.holder">\n')
    if len(includeDirs):
        addIncludes(cProjectFile, includeDirs)
    cProjectFile.write('             <inputType id="org.eclipse.cdt.build.core.settings.holder.inType.' + getId() + '" languageId="org.eclipse.cdt.core.assembly" languageName="Assembly" sourceContentType="org.eclipse.cdt.core.asmSource" superClass="org.eclipse.cdt.build.core.settings.holder.inType"/>\n')
    cProjectFile.write('     </tool>\n')
    cProjectFile.write('     <tool id="org.eclipse.cdt.build.core.settings.holder.' + getId() + '" name="GNU C++" superClass="org.eclipse.cdt.build.core.settings.holder">\n')
    if len(includeDirs):
        addIncludes(cProjectFile, includeDirs)
    cProjectFile.write('             <inputType id="org.eclipse.cdt.build.core.settings.holder.inType.' + getId() + '" languageId="org.eclipse.cdt.core.g++" languageName="GNU C++" sourceContentType="org.eclipse.cdt.core.cxxSource,org.eclipse.cdt.core.cxxHeader" superClass="org.eclipse.cdt.build.core.settings.holder.inType"/>\n')
    cProjectFile.write('     </tool>\n')
    cProjectFile.write('     <tool id="org.eclipse.cdt.build.core.settings.holder.' + getId() + '" name="GNU C" superClass="org.eclipse.cdt.build.core.settings.holder">\n')
    if len(includeDirs):
        addIncludes(cProjectFile, includeDirs)
    cProjectFile.write('             <inputType id="org.eclipse.cdt.build.core.settings.holder.inType.' + getId() + '" languageId="org.eclipse.cdt.core.gcc" languageName="GNU C" sourceContentType="org.eclipse.cdt.core.cSource,org.eclipse.cdt.core.cHeader" superClass="org.eclipse.cdt.build.core.settings.holder.inType"/>\n')
    cProjectFile.write('     </tool>\n')
    cProjectFile.write('    </toolChain>\n')
    cProjectFile.write('   </folderInfo>\n')

    cProjectFile.write('   <sourceEntries>\n')
    if len(excludePackages) > 0:
        excludes = ''
        for package in excludePackages:
            excludes += package + '|'
        cProjectFile.write('<entry excluding="' + excludes[:-1] + '" flags="VALUE_WORKSPACE_PATH|RESOLVED" kind="sourcePath" name=""/>\n')
    cProjectFile.write('   </sourceEntries>\n')

    cProjectFile.write(' </configuration>\n')
    cProjectFile.write('</storageModule>\n')
    cProjectFile.write('<storageModule moduleId="org.eclipse.cdt.core.externalSettings"/>\n')
    cProjectFile.write('</cconfiguration>\n')

def eclipseCdtGenerator(package, argv, extra, bobRoot):
    parser = argparse.ArgumentParser(prog="bob project eclipse-cdt", description='Generate Eclipse CDT Project Files')
    parser.add_argument('-u', '--update', default=False, action='store_true',
                        help="Update project files (.project)")
    parser.add_argument('--buildCfg', action='append', default=[], type=lambda a: a.split("::"),
         help="Adds a new buildconfiguration. Format: <Name>::<flags>")
    parser.add_argument('--overwrite', action='store_true',
        help="Remove destination folder before generating.")
    parser.add_argument('--destination', metavar="DEST",
        help="Destination of project files")
    parser.add_argument('--name', metavar="NAME",
        help="Name of project. Default is complete_path_to_package")
    parser.add_argument('--exclude', default=[], action='append', dest="excludes",
            help="Packages will be marked as 'exclude from build' in eclipse. Usefull if indexer runs OOM.")
    parser.add_argument('-I', dest="additional_includes", default=[], action='append',
        help="Additional include directories. (added recursive starting from this directory)")

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

    projectName = projectName.replace(':', '_')
    if not destination:
        # use package name for project directory
        destination = os.path.join(os.getcwd(), "projects", projectName)
    destination = destination.replace(':', '_')
    if args.overwrite:
        removePath(destination)
    if not os.path.exists(destination):
        os.makedirs(destination)

    buildMeFile = os.path.join(destination, "buildme")

    id = "0." + getId()

    cProjectHeader = '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
    cProjectHeader += '<?fileVersion 4.0.0?><cproject storage_type_id="org.eclipse.cdt.core.XmlProjectDescriptionStorage">'
    cProjectHeader += '<storageModule moduleId="org.eclipse.cdt.core.settings">'

    cProjectFooter  = '</storageModule>\n'
    cProjectFooter += '<storageModule moduleId="cdtBuildSystem" version="4.0.0">\n'
    cProjectFooter += ' <project id="bob4eclipse.null.' + getId() + '" name="bob4eclipse"/>\n'
    cProjectFooter += '</storageModule>\n'
    cProjectFooter += '<storageModule moduleId="scannerConfiguration">\n'
    cProjectFooter += ' <autodiscovery enabled="true" problemReportingEnabled="true" selectedProfileId=""/>\n'
    cProjectFooter += ' <scannerConfigBuildInfo instanceId=" ' + id + '">\n'
    cProjectFooter += '  <autodiscovery enabled="true" problemReportingEnabled="true" selectedProfileId=""/>\n'
    cProjectFooter += ' </scannerConfigBuildInfo>\n'
    cProjectFooter += '</storageModule>\n'
    cProjectFooter += '<storageModule moduleId="org.eclipse.cdt.core.LanguageSettingsProviders"/>\n'
    cProjectFooter += '<storageModule moduleId="refreshScope" versionNumber="2">\n'
    cProjectFooter += ' <configuration configurationName="bob (build only)">\n'
    cProjectFooter += '  <resource resourceType="PROJECT" workspacePath="/bob4eclipse"/>\n'
    cProjectFooter += ' </configuration>\n'
    cProjectFooter += ' <configuration configurationName="Default">\n'
    cProjectFooter += '  <resource resourceType="PROJECT" workspacePath="/bob4eclipse"/>\n'
    cProjectFooter += ' </configuration>\n'
    cProjectFooter += '</storageModule>\n'
    cProjectFooter += '</cproject>\n'

    # setup list of exclude packages
    excludePackages = []
    try:
        for e in args.excludes:
            exp = re.compile(e)
            for name,path in OrderedDict(sorted(dirs, key=lambda t: t[1])).items():
                if exp.search(name):
                    excludePackages.append(name)
        includeDirs = []
        # find additional include dirs
        for i in args.additional_includes:
            if os.path.exists(i):
                includeDirs.append(i)
    except re.error as e:
        raise ParseError("Invalid regular expression '{}': {}".format(e.pattern, e))

    with open(os.path.join(destination, ".cproject"), 'w') as cProjectFile:
        cProjectFile.write(cProjectHeader)
        addCConfig(cProjectFile, excludePackages, includeDirs, "Bob dev", id, "", buildMeFile)
        addCConfig(cProjectFile, excludePackages, includeDirs, "Bob dev (force)", id + "." + getId(), "-f", buildMeFile)
        addCConfig(cProjectFile, excludePackages, includeDirs, "Bob dev (no checkout)",id + "." + getId(), "-b", buildMeFile)
        addCConfig(cProjectFile, excludePackages, includeDirs, "Bob dev (no deps)",id + "." + getId(), "-n", buildMeFile)
        addCConfig(cProjectFile, excludePackages, includeDirs, "Bob dev (no checkout, no deps)",id + "." + getId(), "-bn", buildMeFile)
        addCConfig(cProjectFile, excludePackages, includeDirs, "Bob dev (no checkout, clean)",id + "." + getId(), "-b --clean", buildMeFile)

        for name,flags in args.buildCfg:
            addCConfig(cProjectFile, excludePackages, includeDirs, name, id + "." + getId(), flags, buildMeFile)

        cProjectFile.write(cProjectFooter)

    projectFileHeader = """<?xml version="1.0" encoding="UTF-8"?>
<projectDescription>
<name>""" + projectName + """</name>
<comment></comment>
<projects>
</projects>
<buildSpec>
 <buildCommand>
  <name>org.eclipse.cdt.managedbuilder.core.genmakebuilder</name>
  <arguments>
  </arguments>
 </buildCommand>
 <buildCommand>
  <name>org.eclipse.cdt.managedbuilder.core.ScannerConfigBuilder</name>
  <triggers>full,incremental,</triggers>
  <arguments>
  </arguments>
 </buildCommand>
</buildSpec>
<natures>
 <nature>org.eclipse.cdt.core.cnature</nature>
 <nature>org.eclipse.cdt.core.ccnature</nature>
 <nature>org.eclipse.cdt.managedbuilder.core.managedBuildNature</nature>
 <nature>org.eclipse.cdt.managedbuilder.core.ScannerConfigNature</nature>
</natures>
<linkedResources>"""

    projectFileFooter = """</linkedResources>
    </projectDescription>"""

    with open(os.path.join(destination, ".project"), 'w') as cProjectFile:
        cProjectFile.write(projectFileHeader)
        for name,path in OrderedDict(sorted(dirs, key=lambda t: t[1])).items():
            cProjectFile.write('<link>\n')
            cProjectFile.write(' <name>' + name + '</name>\n')
            cProjectFile.write(' <type>2</type>\n')
            cProjectFile.write(' <location>' + os.path.join(os.getcwd(), path) + '</location>\n')
            cProjectFile.write('</link>\n')
        cProjectFile.write(projectFileFooter)

    if not args.update:
        # Generate buildme
        buildMe = []
        buildMe.append("#!/bin/sh")
        buildMe.append('bob dev "$@" ' + extra + ' ' + quote(project))
        projectCmd = "bob project -n " + extra + " eclipseCdt " + quote(project) + \
            " -u --destination " + quote(destination) + ' --name ' + quote(projectName)
        for i in args.additional_includes:
            projectCmd += " -I " + quote(i)
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
                        if 'executable' in ftype and 'x86' in ftype:
                            createLaunchFile(open(os.path.join(destination, filename + ".launch"), 'w'),
                                os.path.join(os.getcwd(), root, filename), projectName)
                    except OSError:
                        pass

