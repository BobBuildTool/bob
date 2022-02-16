# Bob build tool
# Copyright (C) 2022  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from bob.intermediate import StepIR, PackageIR, RecipeIR, ToolIR, SandboxIR, \
    RecipeSetIR

class MockIR:
    @staticmethod
    def addStep(step, partial):
        return step

    @staticmethod
    def addSandbox(sandbox):
        return sandbox

    @staticmethod
    def addTool(tool):
        return tool

    @staticmethod
    def addPackage(package, partial):
        return package

    @staticmethod
    def addRecipe(recipe):
        return recipe

    @staticmethod
    def addRecipeSet(recipeSet):
        return recipeSet

class MockIRs:
    JENKINS = False

    def mungeStep(self, step):
        return MockIRStep.fromStep(step, MockIR)

    def mungePackage(self, package):
        return MockIRPackage.fromPackage(package, MockIR)

    def mungeRecipe(self, recipe):
        return MockIRRecipe.fromRecipe(recipe, MockIR)

    def mungeSandbox(self, sandbox):
        return sandbox and MockIRSandbox.fromSandbox(sandbox, MockIR)

    def mungeTool(self, tool):
        return MockIRTool.fromTool(tool, MockIR)

    def mungeRecipeSet(self, recipeSet):
        return MockIRRecipeSet.fromRecipeSet(recipeSet)

class MockIRStep(MockIRs, StepIR):
    pass

class MockIRPackage(MockIRs, PackageIR):
    pass

class MockIRRecipe(MockIRs, RecipeIR):
    pass

class MockIRRecipeSet(MockIRs, RecipeSetIR):
    pass

class MockIRTool(MockIRs, ToolIR):
    pass

class MockIRSandbox(MockIRs, SandboxIR):
    pass
