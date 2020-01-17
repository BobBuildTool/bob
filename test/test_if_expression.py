# Bob build tool
# Copyright (C) 2020
#
# SPDX-License-Identifier: GPL-3.0-or-later

from unittest import TestCase
from unittest.mock import MagicMock

from bob.stringparser import DEFAULT_STRING_FUNS, Env, IfExpressionParser
from bob.errors import BobError

class TestIfExpressionParser(TestCase):

    def setUp(self):
        tools = {"a":1, "b":2}
        self.__env = Env({"FOO" : "foo"
            })
        self.__env.setFuns(DEFAULT_STRING_FUNS.copy())
        self.__env.setFunArgs({"sandbox" : False, "__tools" : tools})

        self.__parser = IfExpressionParser()

    def tearDown(self):
        self.__parser = None

    def parse(self, expr):
        return self.__parser.evalExpression(expr, self.__env)

    def testEqual(self):
        self.assertRaises(BobError, self.parse, "x ==")
        self.assertTrue(self.parse('"${FOO}" == "foo"'))
        self.assertTrue(self.parse('"a" == "a"'))
        self.assertFalse(self.parse('"a" == "b"'))

    def testNotEqual(self):
        self.assertRaises(BobError, self.parse, "x !=")
        self.assertFalse(self.parse('"a" != "a"'))
        self.assertTrue(self.parse('"a" != "b"'))

    def testNot(self):
        self.assertRaises(BobError, self.parse, "!")
        self.assertFalse(self.parse('!"true"'))
        self.assertFalse(self.parse("!'TRUE'"))
        self.assertFalse(self.parse("!'1'"))
        self.assertFalse(self.parse("!'foobar'"))
        self.assertTrue(self.parse("!''"))
        self.assertTrue(self.parse("!'0'"))
        self.assertTrue(self.parse("!'false'"))
        self.assertTrue(self.parse("!'FaLsE'"))

    def testOr(self):
        self.assertTrue( self.parse('"true" || "false"'))
        self.assertTrue( self.parse('"false" || "true"'))
        self.assertFalse(self.parse('"false" || "false"'))
        self.assertTrue( self.parse('"1" || "2" || "3" || "4"'))
        self.assertFalse(self.parse('"0" || "0"|| "0" || "0"'))
        self.assertFalse(self.parse('"0" || "" || "false"'))

    def testAnd(self):
        self.assertTrue( self.parse('"true" && "true"'))
        self.assertFalse(self.parse('"true" && "false"'))
        self.assertFalse(self.parse('"false" && "true"'))
        self.assertTrue( self.parse('"true" && "true" && "true"'))
        self.assertTrue( self.parse('"true" && "1" && "abq"'))
        self.assertFalse(self.parse('"true" && ""'))

    def testFuns(self):
        self.assertFalse(self.parse('is-sandbox-enabled()'))
        self.assertTrue(self.parse('is-tool-defined("a")'))
        self.assertFalse(self.parse('is-tool-defined("c")'))
        self.assertFalse(self.parse('match( "string", "pattern")'))
