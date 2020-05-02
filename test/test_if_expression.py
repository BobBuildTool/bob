# Bob build tool
# Copyright (C) 2020
#
# SPDX-License-Identifier: GPL-3.0-or-later

from unittest import TestCase
from unittest.mock import MagicMock

from bob.stringparser import DEFAULT_STRING_FUNS, Env, IfExpression
from bob.errors import BobError

class TestIfExpressionParser(TestCase):

    def setUp(self):
        tools = {"a":1, "b":2}
        self.__env = Env({"FOO" : "foo"
            })
        self.__env.setFuns(DEFAULT_STRING_FUNS.copy())
        self.__env.setFunArgs({"sandbox" : False, "__tools" : tools})

    def evalExpr(self, expr):
        return IfExpression(expr).evalExpression(self.__env)


    def testEqual(self):
        self.assertRaises(BobError, self.evalExpr, 'x ==')
        self.assertTrue(self.evalExpr('"${FOO}" == "foo"'))
        self.assertTrue(self.evalExpr('"a" == "a"'))
        self.assertFalse(self.evalExpr('"a" == "b"'))

    def testNotEqual(self):
        self.assertRaises(BobError, self.evalExpr, "x !=")
        self.assertFalse(self.evalExpr('"a" != "a"'))
        self.assertTrue(self.evalExpr('"a" != "b"'))

    def testNot(self):
        self.assertRaises(BobError, self.evalExpr, "!")
        self.assertFalse(self.evalExpr('!"true"'))
        self.assertFalse(self.evalExpr("!'TRUE'"))
        self.assertFalse(self.evalExpr("!'1'"))
        self.assertFalse(self.evalExpr("!'foobar'"))
        self.assertTrue(self.evalExpr("!''"))
        self.assertTrue(self.evalExpr("!'0'"))
        self.assertTrue(self.evalExpr("!'false'"))
        self.assertTrue(self.evalExpr("!'FaLsE'"))

    def testOr(self):
        self.assertTrue( self.evalExpr('"true" || "false"'))
        self.assertTrue( self.evalExpr('"false" || "true"'))
        self.assertFalse(self.evalExpr('"false" || "false"'))
        self.assertTrue( self.evalExpr('"1" || "2" || "3" || "4"'))
        self.assertFalse(self.evalExpr('"0" || "0"|| "0" || "0"'))
        self.assertFalse(self.evalExpr('"0" || "" || "false"'))

    def testAnd(self):
        self.assertTrue( self.evalExpr('"true" && "true"'))
        self.assertFalse(self.evalExpr('"true" && "false"'))
        self.assertFalse(self.evalExpr('"false" && "true"'))
        self.assertTrue( self.evalExpr('"true" && "true" && "true"'))
        self.assertTrue( self.evalExpr('"true" && "1" && "abq"'))
        self.assertFalse(self.evalExpr('"true" && ""'))

    def testFuns(self):
        self.assertFalse(self.evalExpr('is-sandbox-enabled()'))
        self.assertTrue(self.evalExpr('is-tool-defined("a")'))
        self.assertFalse(self.evalExpr('is-tool-defined("c")'))
        self.assertFalse(self.evalExpr('match( "string", "pattern")'))
        self.assertRaises(BobError, self.evalExpr, "!does-not-exist()")
