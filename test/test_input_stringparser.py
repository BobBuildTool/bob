# Bob build tool
# Copyright (C) 2016  Jan Klötzke
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

from unittest import TestCase
from unittest.mock import MagicMock

from bob.input import StringParser
from bob.input import funEqual, funNotEqual, funNot, funOr, funAnd, \
    funIfThenElse, funSubst, funStrip, funSandboxEnabled, funToolDefined
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
            {})

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
        self.assertEqual(funSandboxEnabled([], sandbox=None), "false")

        attrs = {'isEnabled.return_value' : False}
        disabledSandbox = MagicMock(**attrs)
        self.assertEqual(funSandboxEnabled([], sandbox=disabledSandbox), "false")

        attrs = {'isEnabled.return_value' : True}
        enabledSandbox = MagicMock(**attrs)
        self.assertEqual(funSandboxEnabled([], sandbox=enabledSandbox), "true")

    def testToolDefined(self):
        with self.assertRaises(ParseError):
            funToolDefined([], tools={})
        self.assertEqual(funToolDefined(["a"], tools={"a":1, "b":2}), "true")
        self.assertEqual(funToolDefined(["c"], tools={"a":1, "b":2}), "false")

