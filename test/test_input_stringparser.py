# Bob build tool
# Copyright (C) 2016  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from unittest import TestCase
from unittest.mock import MagicMock

from bob.stringparser import StringParser
from bob.stringparser import funEqual, funNotEqual, funNot, funOr, \
    funAnd, funMatch, funIfThenElse, funSubst, funStrip, \
    funSandboxEnabled, funToolDefined
from bob.errors import ParseError

def echo(args, **options):
    i = 1
    res = []
    for a in args:
        res.append("{}:{}".format(i, a))
        i += 1
    return ";".join(res)

class TestStringParser(TestCase):

    def setUp(self):
        self.p = StringParser(
            {
                "asdf": "qwer",
                "xyz" : "123",
                "null" : "",
                "indirect" : "asdf"
            },
            {"echo" : echo},
            {}, True)

    def tearDown(self):
        self.p = None

    def testNoSubst(self):
        self.assertEqual(self.p.parse("string"), "string")
        self.assertEqual(self.p.parse('asdf"123"gf'), "asdf123gf")
        self.assertEqual(self.p.parse('a\'sd\'f"123"gf'), "asdf123gf")

    def testVariables(self):
        self.assertEqual(self.p.parse("${asdf}"), "qwer")
        self.assertEqual(self.p.parse(">${asdf}<"), ">qwer<")
        self.assertEqual(self.p.parse("..${asdf}..${xyz}.."), "..qwer..123..")

        self.assertEqual(self.p.parse("${asdf:-foobar}"), "qwer")
        self.assertEqual(self.p.parse("${asdf:+foobar}"), "foobar")
        self.assertEqual(self.p.parse("${asdf-foobar}"), "qwer")
        self.assertEqual(self.p.parse("${asdf+foobar}"), "foobar")

        self.assertEqual(self.p.parse("${null:-foobar}"), "foobar")
        self.assertEqual(self.p.parse("${null:+foobar}"), "")
        self.assertEqual(self.p.parse("${null-foobar}"), "")
        self.assertEqual(self.p.parse("${null+foobar}"), "foobar")

        self.assertEqual(self.p.parse("${unset:-foobar}"), "foobar")
        self.assertEqual(self.p.parse("${unset:+foobar}"), "")
        self.assertEqual(self.p.parse("${unset-foobar}"), "foobar")
        self.assertEqual(self.p.parse("${unset+foobar}"), "")

    def testAdvancedVariabled(self):
        self.assertEqual(self.p.parse("${unset:->${asdf}}"), ">qwer")
        self.assertEqual(self.p.parse("""${unset:-">${asdf}"}"""), ">qwer")

    def testIndirectVariables(self):
        self.assertEqual(self.p.parse("${${indirect}}"), "qwer")
        self.assertEqual(self.p.parse("${${indirect}:+alternate}"), "alternate")
        self.assertEqual(self.p.parse("${${asdf}:-default}"), "default")

    def testCommandSubst(self):
        self.assertEqual(self.p.parse("$(echo,foo,bar)"), "1:foo;2:bar")
        self.assertEqual(self.p.parse("$(echo,foo bar )"), "1:foo bar ")
        self.assertEqual(self.p.parse("$(echo,\"foo,bar\" )"), "1:foo,bar ")
        self.assertEqual(self.p.parse("$(echo,foo \"${asdf} bar\" )"), "1:foo qwer bar ")
        self.assertEqual(self.p.parse("$(echo,\'foo ${asdf} bar)\' )"), "1:foo ${asdf} bar) ")
        self.assertEqual(self.p.parse("$(echo,a,${null})"), "1:a;2:")
        self.assertEqual(self.p.parse("$(echo,a \"${null}\" )"), "1:a  ")

    def testEscaping(self):
        self.assertEqual(self.p.parse("as\\df"), "asdf")
        self.assertEqual(self.p.parse("as\\'df"), "as'df")
        self.assertEqual(self.p.parse("\\${null+foobar}"), "${null+foobar}")
        self.assertEqual(self.p.parse("${null:-\\}}"), "}")
        self.assertEqual(self.p.parse("$(echo,foo\\,bar)"), "1:foo,bar")

    def testFails(self):
        self.assertRaises(ParseError, self.p.parse, "$")
        self.assertRaises(ParseError, self.p.parse, "asdf\\")
        self.assertRaises(ParseError, self.p.parse, "as'df")
        self.assertRaises(ParseError, self.p.parse, "$<asdf>")
        self.assertRaises(ParseError, self.p.parse, "${asdf")
        self.assertRaises(ParseError, self.p.parse, "${unknown}")
        self.assertRaises(ParseError, self.p.parse, "${asdf:")
        self.assertRaises(ParseError, self.p.parse, "$()")
        self.assertRaises(ParseError, self.p.parse, "$(unknown)")

class TestStringFunctions(TestCase):

    def testEqual(self):
        self.assertRaises(ParseError, funEqual, [])
        self.assertEqual(funEqual(["a", "a"]), "true")
        self.assertEqual(funEqual(["a", "b"]), "false")

    def testNotEqual(self):
        self.assertRaises(ParseError, funNotEqual, [])
        self.assertEqual(funNotEqual(["a", "a"]), "false")
        self.assertEqual(funNotEqual(["a", "b"]), "true")

    def testNot(self):
        self.assertRaises(ParseError, funNot, [])
        self.assertEqual(funNot(["true"]), "false")
        self.assertEqual(funNot(["TRUE"]), "false")
        self.assertEqual(funNot(["1"]), "false")
        self.assertEqual(funNot(["foobar"]), "false")
        self.assertEqual(funNot([""]), "true")
        self.assertEqual(funNot(["0"]), "true")
        self.assertEqual(funNot(["false"]), "true")
        self.assertEqual(funNot(["FaLsE"]), "true")

    def testOr(self):
        self.assertEqual(funOr(["true", "false"]), "true")
        self.assertEqual(funOr(["false", "true"]), "true")
        self.assertEqual(funOr(["false", "false"]), "false")
        self.assertEqual(funOr(["1", "2", "3", "4"]), "true")
        self.assertEqual(funOr(["0", "0", "0", "0"]), "false")
        self.assertEqual(funOr(["0", "", "false"]), "false")

    def testAnd(self):
        self.assertEqual(funAnd(["true", "true"]), "true")
        self.assertEqual(funAnd(["true", "false"]), "false")
        self.assertEqual(funAnd(["false", "true"]), "false")
        self.assertEqual(funAnd(["true", "true", "true"]), "true")
        self.assertEqual(funAnd(["true", "1", "abq"]), "true")
        self.assertEqual(funAnd(["true", ""]), "false")

    def testMatch(self):
        self.assertEqual(funMatch(["string", "pattern"]), "false")
        self.assertEqual(funMatch(["string", "trin"]), "true")
        self.assertEqual(funMatch(["string", "tr(i|j|k)n"]), "true")
        self.assertEqual(funMatch(["string", "tr[ijk]n"]), "true")
        self.assertEqual(funMatch(["xyyz", "^xy{2}z$"]), "true")
        self.assertEqual(funMatch(["xyyz", "^xy{1}z$"]), "false")
        self.assertEqual(funMatch(["abc", "."]), "true")
        self.assertEqual(funMatch(["abc", ".+"]), "true")
        self.assertEqual(funMatch([".", "\."]), "true")
        self.assertEqual(funMatch(["a.", "^\."]), "false")
        self.assertEqual(funMatch(["(a)", "\(a"]), "true")
        self.assertEqual(funMatch(["(a)", "\(a$"]), "false")
        self.assertEqual(funMatch(["\\a)", "\\\\a\)"]), "true")
        self.assertEqual(funMatch(["ABC", "a", "i"]), "true")
        self.assertRaises(ParseError, funMatch, ["a"])
        self.assertRaises(ParseError, funMatch, ["a","b","x"])
        self.assertRaises(ParseError, funMatch, ["a","b","i","y"])

    def testIfThenElse(self):
        self.assertRaises(ParseError, funIfThenElse, ["a", "b"])
        self.assertEqual(funIfThenElse(["true", "a", "b"]), "a")
        self.assertEqual(funIfThenElse(["qwer", "a", "b"]), "a")
        self.assertEqual(funIfThenElse(["false", "a", "b"]), "b")
        self.assertEqual(funIfThenElse(["0", "a", "b"]), "b")
        self.assertEqual(funIfThenElse(["", "a", "b"]), "b")

    def testSubst(self):
        self.assertRaises(ParseError, funSubst, ["a"])
        self.assertEqual(funSubst(["ee","EE","feet on the street"]),
            "fEEt on the strEEt")

    def testStrip(self):
        self.assertRaises(ParseError, funStrip, ["a", "b"])
        self.assertEqual(funStrip(["  asdf  "]), "asdf")

    def testSandboxEnabled(self):
        with self.assertRaises(ParseError):
            funSandboxEnabled(["1", "2"], sandbox=None)
        self.assertEqual(funSandboxEnabled([], sandbox=False), "false")
        self.assertEqual(funSandboxEnabled([], sandbox=True), "true")

    def testToolDefined(self):
        with self.assertRaises(ParseError):
            funToolDefined([], tools={})
        self.assertEqual(funToolDefined(["a"], tools={"a":1, "b":2}), "true")
        self.assertEqual(funToolDefined(["c"], tools={"a":1, "b":2}), "false")

