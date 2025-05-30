# Bob build tool
# Copyright (C) 2016  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from unittest import TestCase
from unittest.mock import MagicMock

from bob.stringparser import StringParser
from bob.stringparser import funEqual, funNotEqual, funNot, funOr, \
    funAnd, funMatch, funIfThenElse, funSubst, funStrip, \
    funSandboxEnabled, funToolDefined, funToolEnv, funResubst
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
                "indirect" : "asdf",
                "%%" : "percent",
            },
            {"echo" : echo},
            {}, True)

    def tearDown(self):
        self.p = None

    def testNoSubst(self):
        self.assertEqual(self.p.parse("string"), "string")
        self.assertEqual(self.p.parse("'string'"), "string")
        self.assertEqual(self.p.parse('asdf"123"gf'), "asdf123gf")
        self.assertEqual(self.p.parse('a\'sd\'f"123"gf'), "asdf123gf")
        self.assertEqual(self.p.parse("'${asdf}'"), "${asdf}")
        self.assertEqual(self.p.parse("'$(echo,foo,bar)'"), "$(echo,foo,bar)")
        self.assertEqual(self.p.parse("a's\"d\"f'g"), "as\"d\"fg")

    def testVariables(self):
        self.assertEqual(self.p.parse("${asdf}"), "qwer")
        self.assertEqual(self.p.parse("${%%}"), "percent")
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

        # bare variables
        self.assertEqual(self.p.parse("$asdf"), "qwer")
        self.assertEqual(self.p.parse(">$asdf<"), ">qwer<")
        self.assertEqual(self.p.parse("..$asdf..$xyz.."), "..qwer..123..")

    def testUnsetOk(self):
        u = StringParser({}, {}, {}, False)
        self.assertEqual(u.parse("${asdf}"), "")
        self.assertEqual(u.parse(">${asdf}<"), "><")
        self.assertEqual(u.parse(">$asdf<"), "><")

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

        # bare variables that should fail even if unset variables are allowed
        u = StringParser({}, {}, {}, False)
        self.assertRaises(ParseError, u.parse, "$1")
        self.assertRaises(ParseError, u.parse, "$%%")

    def testSkipUnused(self):
        """Unused branches must not be substituted.

        Syntax error must still be detected, though.
        """

        self.assertEqual(self.p.parse("${asdf:-$unset}"), "qwer")
        self.assertEqual(self.p.parse("${asdf:-${unset}}"), "qwer")
        self.assertEqual(self.p.parse("${asdf:-${${double-unset}}}"), "qwer")
        self.assertEqual(self.p.parse("${asdf:-$(unknown)}"), "qwer")
        self.assertEqual(self.p.parse("${asdf:-$($fn,$unset)}"), "qwer")
        self.assertRaises(ParseError, self.p.parse, "${asdf:-$($fn}")

        self.assertEqual(self.p.parse("${unset:+$unset}"), "")
        self.assertEqual(self.p.parse("${unset:+${unset}}"), "")
        self.assertEqual(self.p.parse("${unset:+${${double-unset}}}"), "")
        self.assertEqual(self.p.parse("${unset:+$(unknown)}"), "")
        self.assertEqual(self.p.parse("${unset:+$($fn,$unset)}"), "")
        self.assertRaises(ParseError, self.p.parse, "${unset:+${${double-unset}}")

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
        self.assertEqual(funMatch([".", r"\."]), "true")
        self.assertEqual(funMatch(["a.", r"^\."]), "false")
        self.assertEqual(funMatch(["(a)", r"\(a"]), "true")
        self.assertEqual(funMatch(["(a)", r"\(a$"]), "false")
        self.assertEqual(funMatch([r"\a)", r"\\a\)"]), "true")
        self.assertEqual(funMatch(["ABC", "a", "i"]), "true")
        self.assertRaises(ParseError, funMatch, ["a"])
        self.assertRaises(ParseError, funMatch, ["a","b","x"])
        self.assertRaises(ParseError, funMatch, ["a","b","i","y"])

        # broken regex
        with self.assertRaises(ParseError):
            funMatch(["b", r'\c'])

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
            funToolDefined([], __tools={})
        self.assertEqual(funToolDefined(["a"], __tools={"a":1, "b":2}), "true")
        self.assertEqual(funToolDefined(["c"], __tools={"a":1, "b":2}), "false")

    def testToolEnv(self):
        # Wrong number of arguments
        with self.assertRaises(ParseError):
            funToolEnv(["foo"], __tools={})
        with self.assertRaises(ParseError):
            funToolEnv(["foo", "bar", "baz", "extra"], __tools={})

        # Undefined tool
        with self.assertRaises(ParseError):
            funToolEnv(["foo", "bar"], __tools={})

        t = MagicMock()
        t.environment = { "bar" : "baz" }
        tools = { "foo" : t }

        # Undefined variable in tool
        with self.assertRaises(ParseError):
            funToolEnv(["foo", "nx"], __tools=tools)

        # Undefined variable in tool with default
        self.assertEqual(funToolEnv(["foo", "nx", "default"], __tools=tools),
                         "default")

        # Get real var
        self.assertEqual(funToolEnv(["foo", "bar"], __tools=tools), "baz")
        self.assertEqual(funToolEnv(["foo", "bar", "def"], __tools=tools), "baz")

    def testResubst(self):
        # Wrong number of arguments
        with self.assertRaises(ParseError):
            funResubst(["foo", "bar"])
        with self.assertRaises(ParseError):
            funResubst(["foo", "bar", "baz", "extra", "toomuch"])

        # Unsupported flag
        with self.assertRaises(ParseError):
            funResubst(["a", "b", "abc", "%"])

        # broken regex
        with self.assertRaises(ParseError):
            funResubst([r'\c', "b", "abc"])

        self.assertEqual(funResubst(["X", "Y", "AXBXCX"]), "AYBYCY")
        self.assertEqual(funResubst([r"\.[^.]+$", "", "1.2.3"]), "1.2")
        self.assertEqual(funResubst(["[X]", "Y", "AXBx"]), "AYBx")
        self.assertEqual(funResubst(["[x]", "Y", "AXBx", "i"]), "AYBY")
