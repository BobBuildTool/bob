# Bob build tool
# Copyright (C) 2022 Kai Kürschner
#
# SPDX-License-Identifier: GPL-3.0-or-later

from .common import CommonIDEGenerator
from ..utils import quoteCmdExe, isMsys, isWindows
from pathlib import Path, PureWindowsPath
from shlex import quote as quoteBash

import os
import sys
import json

MSYS_ARGUMENTS = ["-msys2", "-defterm", "-no-start", "-use-full-path", "-shell", "bash", "-where"]

# global template for the whole workspace file
JSON_WORKSPACE_TEMPLATE = {
    "folders": [],
    "launch": {
        "configurations": [],
        "compunds": []
    },
    "tasks": {
        "version": "2.0.0",
        "options": {
            "shell" : {
                "executable": "",
                "args": []
            },
            "cwd": ""
        },
        "tasks": [],
        "inputs": [ {
            "id": "buildArguments",
            "type": "promptString",
            "description": "Please give 'bob dev' arguments (see 'bob dev --help' for more information)",
            "default": "-v"
        } 
        ]
    },
    "settings": {
        "C_Cpp.default.includePath" : [],
        "C_Cpp.default.defines": []
    }
}

JSON_TASK_TEMPLATE = {
    "label": "",
    "type" : "shell",
    "command": "",
    "args": [""],
    "problemMatcher": [
        "$msCompile",
        "$gcc"
    ],
    "group": {
        "kind": "build",
        "isDefault": True
    }
}

TASK_LIST = [
    {"label" :"Bob dev", "args": ["-v"] },
    {"label" :"Bob dev (force)", "args": ["-v", "-f"] },
    {"label" :"Bob dev (no checkout)", "args": ["-v", "-b"] },
    {"label" :"Bob dev (no deps)", "args": ["-v", "-n"] },
    {"label" :"Bob dev (no checkout, no deps)", "args": ["-v", "-nb"] },
    {"label" :"Bob dev (no checkout, clean)", "args": ["-v", "-b", "--clean"] },
    {"label" :"Bob dev (ask for arguments)", "args": ["${input:buildArguments}"] },
]

def getCorrectPathList(recipesRoot, pathlist):
    if len(pathlist) == 0: return []
    if isMsys():
        values = os.popen('cygpath -w {}'.format(" ".join([quoteBash(i) for i in pathlist]))).read().strip().split("\n")
        return [str(recipesRoot.joinpath(PureWindowsPath(i))) for i in values]
    else:
        return [str(recipesRoot.joinpath(i)) for i in pathlist]

def getCorrectPath(recipesRoot, path):
    if isMsys() and len(path) > 0:
        return  recipesRoot.joinpath(PureWindowsPath(os.popen('cygpath -w {}'.format(quoteBash(path))).read().strip()))
    else:
        return recipesRoot.joinpath(path)

class Project:
    def __init__(self, recipesRoot, scan, includeList):
        self.isRoot = scan.isRoot
        self.packagePath = scan.stack
        self.workspacePath =  getCorrectPath(recipesRoot, scan.workspacePath)
        self.headers = getCorrectPathList(recipesRoot, scan.headers)
        self.sources = getCorrectPathList(recipesRoot, scan.sources)
        self.resources = getCorrectPathList(recipesRoot, scan.resources)
        self.incPaths =  getCorrectPathList(recipesRoot, scan.incPaths)
        self.dependencies = scan.dependencies
        self.runTargets = getCorrectPathList(recipesRoot, scan.runTargets)
        includeList += self.incPaths

class VsCodeGenerator(CommonIDEGenerator):
    def __init__(self):
        super().__init__("vscode", "Generate Visual Studio Code workspace")
        self.parser.add_argument('--sort', action='store_true', help="Sort the dependencies by name (default: unsorted)")

    def configure(self, package, argv):
        super().configure(package, argv)

    def generate(self, extra, bobRoot):
        super().generate()

        # gather root paths
        bobPwd = Path(os.getcwd())
        if isMsys():
            if os.getenv('WD') is None:
                raise BuildError("Cannot create Visual Studio Code project for Windows! MSYS2 must be started by msys2_shell.cmd script!")
            msysRoot = PureWindowsPath(os.getenv('WD')) / '..' / '..'
            winPwd = PureWindowsPath(os.popen('pwd -W').read().strip())
            shell = str(msysRoot / "msys2_shell.cmd")
            shellArgs = (MSYS_ARGUMENTS + [str(winPwd), "-l", "-c"])
        elif isWindows():
            winPwd = bobPwd
            winDestination = Path(self.destination).resolve()
            shell = "cmd.exe"
            shellArgs = []
        else:
            winPwd = bobPwd
            winDestination = Path(self.destination).resolve()
            shell = "sh"
            shellArgs = ["-c"]

        JSON_WORKSPACE_TEMPLATE["tasks"]["options"]["cwd"] = str(winPwd)
        JSON_WORKSPACE_TEMPLATE["tasks"]["options"]["shell"]["executable"] = shell
        JSON_WORKSPACE_TEMPLATE["tasks"]["options"]["shell"]["args"] = shellArgs

        includeList = self.prependIncludeDirectories
        projects = {
            name : Project(winPwd, scan, includeList)
            for name,scan in self.packages.items()
        }

        JSON_WORKSPACE_TEMPLATE["settings"]["C_Cpp.default.includePath"] = includeList + self.appendIncludeDirectories

        workspaceProjectList = []
        for name,project in sorted(projects.items()) if self.args.sort else projects.items():
            workspaceProjectList.append({
                "name": name,
                "path": str(project.workspacePath)
            })

        JSON_WORKSPACE_TEMPLATE["folders"] = workspaceProjectList
        JSON_WORKSPACE_TEMPLATE["tasks"]["tasks"] = self.__getTaskList(bobRoot, "/".join(self.rootPackage.getStack()))

        self.updateFile(os.path.join(self.destination, self.projectName+".code-workspace"),
                json.dumps(JSON_WORKSPACE_TEMPLATE, indent=4),
                encoding="utf-8", newline='\r\n')

    def __getTaskList(self, bobRoot, package):
        taskList = []
        for entry in TASK_LIST:
            task = JSON_TASK_TEMPLATE.copy()
            task["label"] = entry["label"]
            if isMsys():
                task["command"]= " ".join([bobRoot, "dev", package] + entry["args"])
            else:
                task["command"]= bobRoot
                task["args"]= ["dev", package] + entry["args"]

            taskList.append(task)

        return taskList

def vsCodeProjectGenerator(package, argv, extra, bobRoot):
    generator = VsCodeGenerator()
    generator.configure(package, argv)
    generator.generate(extra, bobRoot)
