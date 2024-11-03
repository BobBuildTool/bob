# Bob build tool
# Copyright (C) 2022  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ...archive import JenkinsArchive
from ...intermediate import StepIR, PackageIR, RecipeIR, ToolIR, SandboxIR, RecipeSetIR
from ...scm import auditFromData
from ...utils import runInEventLoop

def getJenkinsVariantId(step):
    """Get the variant-id of a step with it's sandbox dependency.

    Even though the sandbox is considered an invariant of the build, it still
    needs to be respected when building the packages. Because the Jenkins logic
    cannot rely on lazy evaluation like the local builds we need to regard the
    sandbox as real dependency.

    This is only relevant when calculating the job graph. The actual build
    result still uses the original variant- and build-id.
    """
    vid = step.getVariantId()
    sandbox = step.getSandbox()
    if sandbox:
        vid += sandbox.getStep().getVariantId()
    return vid

class PartialIRBase:
    JENKINS = True

    def mungeStep(self, step):
        ret = PartialStep.fromData(self.graph.steps[step])
        ret.graph = self.graph
        return ret

    def mungePackage(self, package):
        ret = PartialPackage.fromData(self.graph.packages[package])
        ret.graph = self.graph
        return ret

    def mungeRecipe(self, recipe):
        ret = PartialRecipe.fromData(self.graph.recipes[recipe])
        ret.graph = self.graph
        return ret

    def mungeSandbox(self, sandbox):
        if sandbox is None:
            ret = None
        else:
            ret = PartialSandbox.fromData(sandbox)
            ret.graph = self.graph
        return ret

    def mungeTool(self, tool):
        ret = PartialTool.fromData(tool)
        ret.graph = self.graph
        return ret

    def mungeRecipeSet(self, recipeSet):
        ret = PartialRecipeSet.fromData(self.graph.recipeSet, self.graph.scmAudit)
        ret.graph = self.graph
        return ret

class PartialStep(PartialIRBase, StepIR):
    pass

class PartialPackage(PartialIRBase, PackageIR):
    pass

class PartialRecipe(PartialIRBase, RecipeIR):
    pass

class PartialRecipeSet(PartialIRBase, RecipeSetIR):
    @classmethod
    def fromData(cls, data, scmAudit):
        self = super(PartialRecipeSet, cls).fromData(data)
        self.__scmAudit = scmAudit and { name : (audit and auditFromData(audit))
                                         for name, audit in scmAudit.items() }
        return self

    async def getScmAudit(self):
        return self.__scmAudit

class PartialTool(PartialIRBase, ToolIR):
    pass

class PartialSandbox(PartialIRBase, SandboxIR):
    pass

class PartialIR:

    def __init__(self):
        self.roots = []
        self.steps = {}
        self.packages = {}
        self.recipes = {}
        self.recipeSet = None
        self.scmAudit = {}

    @classmethod
    def fromData(cls, data):
        self = cls()
        self.roots = data['roots']
        self.steps = data['steps']
        self.packages = data['packages']
        self.recipes = data['recipes']
        self.recipeSet = data['recipeSet']
        return self

    def toData(self):
        return {
            'roots' : self.roots,
            'steps' : self.steps,
            'packages' : self.packages,
            'recipes' : self.recipes,
            'recipeSet' : self.recipeSet
        }

    def add(self, packageStep):
        assert packageStep.isPackageStep()
        vid = getJenkinsVariantId(packageStep).hex()
        if vid not in self.roots:
            self.addStep(packageStep, False)
            self.roots.append(vid)

    def addStep(self, step, partial):
        """Add a package step to the partial graph.

        The steps of the package of this packageStep are added and the
        packageStep of all dependencies. The dependencies are only partially
        saved!
        """

        vid = getJenkinsVariantId(step).hex()
        data = self.steps.get(vid)
        # Attention: this will typically recurse back here from the
        # PartialStep.fromData constructor. We store a sentinel first and
        # only allow upgrades from partial to non-partial dumps.
        if (data is None) or \
           (not partial and ((isinstance(data, bool) and data) or \
                             (not isinstance(data, bool) and PartialStep.fromData(data).partial))):
            # new or upgrade partial -> !partial
            self.steps[vid] = partial
            new = PartialStep.fromStep(step, self, partial)
            if self.steps[vid] == partial:
                self.steps[vid] = new.toData()
        return vid

    def addSandbox(self, sandbox):
        return sandbox and PartialSandbox.fromSandbox(sandbox, self).toData()

    def addTool(self, tool):
        return PartialTool.fromTool(tool, self).toData()

    def addPackage(self, package, partial):
        # Attention: this will typically recurse like addStep().
        key = "/".join(package.getStack())
        data = self.packages.get(key)
        if (data is None) or \
           (not partial and ((isinstance(data, bool) and data) or \
                             (not isinstance(data, bool) and PartialPackage.fromData(data).partial))):
            self.packages[key] = partial
            new = PartialPackage.fromPackage(package, self, partial)
            if self.packages[key] == partial:
                self.packages[key] = new.toData()
        return key

    def addRecipe(self, recipe):
        key = recipe.getPackageName()
        if key not in self.recipes:
            self.recipes[key] = PartialRecipe.fromRecipe(recipe, self).toData()
        return key

    def addRecipeSet(self, recipeSet):
        if self.recipeSet is None:
            self.recipeSet = PartialRecipeSet.fromRecipeSet(recipeSet).toData()
        return None

    def getRoots(self):
        ret = [ PartialStep.fromData(self.steps[s]) for s in self.roots ]
        for i in ret: i.graph = self
        return ret

    def getAllSteps(self):
        ret = [ PartialStep.fromData(step) for step in self.steps.values() ]
        for i in ret: i.graph = self
        return ret

    def getAllWorkspaces(self):
        return [ step.getWorkspacePath() for step in self.getAllSteps() ]

    def getTransferFiles(self):
        """Get list of all files that potentially need to be copied *into* the job.

        All results of the Job need to be re-created every time.
        """
        ret = []
        for step in (PartialStep.fromData(s) for s in self.steps.values()):
            if not step.isPackageStep(): continue
            if not step.partial: continue
            ret.append(JenkinsArchive.tgzName(step))
            ret.append(JenkinsArchive.buildIdName(step))
        return ret

    def getBuiltCheckoutSteps(self):
        ret = [ s for s in (PartialStep.fromData(i) for i in self.steps.values())
                        if not s.partial and s.isCheckoutStep() ]
        for i in ret: i.graph = self
        return ret

    def getRecipeSet(self):
        ret = PartialRecipeSet.fromData(self.recipeSet, self.scmAudit)
        ret.graph = self
        return ret
