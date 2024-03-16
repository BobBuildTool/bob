# Bob build tool
# Copyright (C) 2016  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from unittest import TestCase
from unittest.mock import Mock

from bob.input import Env, CoreCheckoutStep, CoreBuildStep, CorePackageStep

class Empty:
    pass

class MockRecipeSet:
    def scmOverrides(self):
        return []

class MockRecipe:
    def getRecipeSet(self):
        return MockRecipeSet()

    checkoutAsserts = []
    toolDepCheckout = set(["a", "zz"])
    toolDepCheckoutWeak = set()
    toolDepBuild = set(["a", "zz"])
    toolDepBuildWeak = set()
    toolDepPackage = set(["a", "zz"])
    toolDepPackageWeak = set()

class MockCorePackage:
    def __init__(self, checkoutScript="script", checkoutDigestScript="digest",
            checkoutDeterministic=True, tools={}):
        self.name = "package"
        self.recipe = MockRecipe()
        self.recipe.checkoutScript = checkoutScript
        self.recipe.checkoutDigestScript = checkoutDigestScript
        self.recipe.checkoutDeterministic = checkoutDeterministic
        self.tools = tools
        self.sandbox = None

    def getName(self):
        return self.name

    def refDeref(self, stack, inputTools, inputSandbox, pathFormatter):
        return MockPackage()

class MockPackage:
    def _setCheckoutStep(self, step):
        pass
    def _setBuildStep(self, step):
        pass
    def _setPackageStep(self, step):
        pass

nullPkg = MockCorePackage()
nullFmt = lambda s,t: ""


class TestCheckoutStep(TestCase):
    def testStereotype(self):
        """Check that the CheckoutStep identifies itself correctly"""
        s = CoreCheckoutStep(nullPkg).refDeref([], {}, None, nullFmt)
        assert s.isCheckoutStep() == True
        assert s.isBuildStep() == False
        assert s.isPackageStep() == False

    def testTrivialDeterministic(self):
        """Trivial steps are deterministic"""
        s = CoreCheckoutStep(nullPkg).refDeref([], {}, None, nullFmt)
        assert s.isDeterministic()

    def testTrivialInvalid(self):
        """Trivial steps are invalid"""
        s = CoreCheckoutStep(nullPkg).refDeref([], {}, None, nullFmt)
        assert s.isValid() == False

    def testDigestStable(self):
        """Same input should yield same digest"""
        s1 = CoreCheckoutStep(nullPkg, ("script", "digest", [], []), [],
            Env({"a" : "asdf", "q": "qwer" }), Env({ "a" : "asdf" }))
        s2 = CoreCheckoutStep(nullPkg, ("script", "digest", [], []), [],
            Env({"a" : "asdf", "q": "qwer" }), Env({ "a" : "asdf" }))
        assert s1.variantId == s2.variantId

    def testDigestScriptChange(self):
        """Script does influnce the digest"""
        s1 = CoreCheckoutStep(nullPkg, ("script", "digest", [], []), [],
            Env({"a" : "asdf", "q": "qwer" }), Env({ "a" : "asdf" }))
        evilPkg = MockCorePackage(checkoutScript="evil", checkoutDigestScript="other digest")
        s2 = CoreCheckoutStep(evilPkg, ("evil", "other digest", [], []), [],
            Env({"a" : "asdf", "q": "qwer" }), Env({ "a" : "asdf" }))
        assert s1.variantId != s2.variantId

    def testDigestFullEnv(self):
        """Full env does not change digest. It is only used for SCMs."""
        s1 = CoreCheckoutStep(nullPkg, ("script", "digest", [], []), [],
            Env({"a" : "asdf", "q": "qwer" }), Env({ "a" : "asdf" }))
        s2 = CoreCheckoutStep(nullPkg, ("script", "digest", [], []), [],
            Env(), Env({ "a" : "asdf" }))
        assert s1.variantId == s2.variantId

    def testDigestEnv(self):
        """Env changes digest"""
        s1 = CoreCheckoutStep(nullPkg, ("script", "digest", [], []), [],
            digestEnv=Env({ "a" : "asdf" }))

        # different value
        s2 = CoreCheckoutStep(nullPkg, ("script", "digest", [], []), [],
            digestEnv=Env({ "a" : "qwer" }))
        assert s1.variantId != s2.variantId

        # added entry
        s2 = CoreCheckoutStep(nullPkg, ("script", "digest", [], []), [],
            digestEnv=Env({ "a" : "asdf", "b" : "qwer" }))
        assert s1.variantId != s2.variantId

        # removed entry
        s2 = CoreCheckoutStep(nullPkg, ("script", "digest", [], []), [],
            digestEnv=Env())
        assert s1.variantId != s2.variantId

    def testDigestEnvRotation(self):
        """Rotating characters between key-value pairs must be detected"""
        s1 = CoreCheckoutStep(nullPkg, ("script", "digest", [], []), [],
            digestEnv=Env({ "a" : "bc", "cd" : "e" }))
        s2 = CoreCheckoutStep(nullPkg, ("script", "digest", [], []), [],
            digestEnv=Env({ "a" : "bcc", "d" : "e" }))
        assert s1.variantId != s2.variantId

        s1 = CoreCheckoutStep(nullPkg, ("script", "digest", [], []), [],
            digestEnv=Env({ "a" : "bb", "c" : "dd", "e" : "ff" }))
        s2 = CoreCheckoutStep(nullPkg, ("script", "digest", [], []), [],
            digestEnv=Env({ "a" : "bbc=dd", "e" : "ff" }))
        assert s1.variantId != s2.variantId

    def testDigestEmpyEnv(self):
        """Adding empty entry must be detected"""
        s1 = CoreCheckoutStep(nullPkg, ("script", "digest", [], []), [],
            digestEnv=Env({ "a" : "b" }))
        s2 = CoreCheckoutStep(nullPkg, ("script", "digest", [], []), [],
            digestEnv=Env({ "a" : "b", "" : "" }))
        assert s1.variantId != s2.variantId

    def testDigestTools(self):
        """Tools must influence digest"""

        t1 = Empty()
        t1.coreStep = Empty()
        t1.coreStep.variantId = b'0123456789abcdef'
        t1.coreStep.isDeterministic = lambda: True
        t1.path = "p1"
        t1.libs = []

        p1 = MockCorePackage(tools={"a" : t1})
        s1 = CoreCheckoutStep(p1, ("script", "digest", [], []))

        # tool name has no influence
        p2 = MockCorePackage(tools={"zz" : t1})
        s2 = CoreCheckoutStep(p2, ("script", "digest", [], []))
        assert s1.variantId == s2.variantId

        # step digest change
        t2 = Empty()
        t2.coreStep = Empty()
        t2.coreStep.variantId = b'0123456789000000'
        t2.coreStep.isDeterministic = lambda: True
        t2.path = "p1"
        t2.libs = []

        p2 = MockCorePackage(tools={"a" : t2})
        s2 = CoreCheckoutStep(p2, ("script", "digest", [], []))
        assert s1.variantId != s2.variantId

        # path change
        t2.coreStep.variantId = b'0123456789abcdef'
        t2.path = "foo"
        t2.libs = []

        s2 = CoreCheckoutStep(p2, ("script", "digest", [], []))
        assert s1.variantId != s2.variantId

        # libs change
        t2.coreStep.getVariantId = b'0123456789abcdef'
        t2.path = "p1"
        t2.libs = ["asdf"]

        s2 = CoreCheckoutStep(p2, ("script", "digest", [], []))
        assert s1.variantId != s2.variantId

