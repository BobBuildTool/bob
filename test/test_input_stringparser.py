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

from bob.parser import StringParser, substituteParseResult
from bob.input import funEqual, funNotEqual, funNot, funOr, \
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
        self.p = StringParser()

        self.env = {
            "asdf": "qwer",
            "ASDF": "QWER",
            "xyxx": "xyxx",
            "xyz" : "123",
            "null" : "",
            "indirect" : "asdf",
            "special1"  : "{{//",
            "special2"  : "{{}}"
            }

        self.funs = { "echo" : echo }

        self.funArgs = {}

    def tearDown(self):
        self.p = None

    def parse( self, input ):
        text, tokens = self.p.parse( input )
        return substituteParseResult( tokens, self.env, self.funs, self.funArgs )

    def testNoSubst(self):
        self.assertEqual(self.parse("string"), "string")
        self.assertEqual(self.parse('asdf"123"gf'), "asdf123gf")
        self.assertEqual(self.parse('a\'sd\'f"123"gf'), "asdf123gf")

    def testVariables(self):
        self.assertEqual(self.parse("${asdf}"), "qwer")
        self.assertEqual(self.parse(">${asdf}<"), ">qwer<")
        self.assertEqual(self.parse("..${asdf}..${xyz}.."), "..qwer..123..")

        self.assertEqual(self.parse("${asdf:-foobar}"), "qwer")
        self.assertEqual(self.parse("${asdf:+foobar}"), "foobar")
        self.assertEqual(self.parse("${asdf-foobar}"), "qwer")
        self.assertEqual(self.parse("${asdf+foobar}"), "foobar")

        self.assertEqual(self.parse("${null:-foobar}"), "foobar")
        self.assertEqual(self.parse("${null:+foobar}"), "")
        self.assertEqual(self.parse("${null-foobar}"), "")
        self.assertEqual(self.parse("${null+foobar}"), "foobar")

        self.assertEqual(self.parse("${unset:-foobar}"), "foobar")
        self.assertEqual(self.parse("${unset:+foobar}"), "")
        self.assertEqual(self.parse("${unset-foobar}"), "foobar")
        self.assertEqual(self.parse("${unset+foobar}"), "")

        self.assertEqual(self.parse("${asdf^}"), "Qwer")
        self.assertEqual(self.parse("${asdf^^}"), "QWER")
        self.assertEqual(self.parse("${ASDF,}"), "qWER")
        self.assertEqual(self.parse("${ASDF,,}"), "qwer")

        self.assertEqual(self.parse("${xyxx%x}"), "xyx")
        self.assertEqual(self.parse("${xyxx%y}"), "xyxx")
        self.assertEqual(self.parse("${xyxx%y*}"), "x")
        self.assertEqual(self.parse("${xyxx%x*}"), "xyx")
        self.assertEqual(self.parse("${xyxx%*x}"), "xyx")
        self.assertEqual(self.parse("${xyxx%*}"), "xyx")
        self.assertEqual(self.parse("${xyxx%a}"), "xyxx")
        self.assertEqual(self.parse("${xyxx%a*}"), "xyxx")
        self.assertEqual(self.parse("${xyxx%*a}"), "xyxx")
        self.assertEqual(self.parse("${xyxx%??}"), "xy")
        self.assertEqual(self.parse("${xyxx%\??}"), "xyxx")
        self.assertEqual(self.parse("${xyxx%%x*}"), "")
        self.assertEqual(self.parse("${xyxx%%*x}"), "")
        self.assertEqual(self.parse("${xyxx%%y*}"), "x")
        self.assertEqual(self.parse("${xyxx%%*}"), "")
        self.assertEqual(self.parse("${xyxx%%a}"), "xyxx")
        self.assertEqual(self.parse("${xyxx%%a*}"), "xyxx")
        self.assertEqual(self.parse("${xyxx%%*a}"), "xyxx")
        self.assertEqual(self.parse("${xyxx%%??}"), "xy")
        self.assertEqual(self.parse("${xyxx%%?\?}"), "xyxx")
        self.assertEqual(self.parse("${null%a*}"), "")
        self.assertEqual(self.parse("${null%%a*}"), "")

        self.assertEqual(self.parse("${xyxx#x}"), "yxx")
        self.assertEqual(self.parse("${xyxx#x*}"), "yxx")
        self.assertEqual(self.parse("${xyxx#*x}"), "yxx")
        self.assertEqual(self.parse("${xyxx#*y}"), "xx")
        self.assertEqual(self.parse("${xyxx#y*}"), "xyxx")
        self.assertEqual(self.parse("${xyxx#a}"), "xyxx")
        self.assertEqual(self.parse("${xyxx#*}"), "yxx")
        self.assertEqual(self.parse("${xyxx##*x}"), "")
        self.assertEqual(self.parse("${xyxx##x*}"), "")
        self.assertEqual(self.parse("${xyxx##y*}"), "xyxx")
        self.assertEqual(self.parse("${xyxx##*y}"), "xx")
        self.assertEqual(self.parse("${xyxx##y*}"), "xyxx")
        self.assertEqual(self.parse("${xyxx##a}"), "xyxx")
        self.assertEqual(self.parse("${xyxx##*}"), "")
        self.assertEqual(self.parse("${null#a*}"), "")
        self.assertEqual(self.parse("${null##a*}"), "")

        self.assertEqual(self.parse("${special1/\//}"), "{{/")
        self.assertEqual(self.parse("${special1//\//}"), "{{")
        self.assertEqual(self.parse("${special1%/}"), "{{/")
        self.assertEqual(self.parse("${special1%%/*}"), "{{")
        self.assertEqual(self.parse("${special2%\}}"), "{{}")
        self.assertEqual(self.parse("${special2%%\}*}"), "{{")
        self.assertEqual(self.parse("${special2#\{}"), "{}}")
        self.assertEqual(self.parse("${special2##*\{}"), "}}")

        self.assertEqual(self.parse("${xyxx/x/a}"), "ayxx")
        self.assertEqual(self.parse("${xyxx//x/a}"), "ayaa")
        self.assertEqual(self.parse("${xyxx/[a-z]/a}"), "ayxx")
        self.assertEqual(self.parse("${xyxx//[a-z]/a}"), "aaaa")
        self.assertEqual(self.parse("${xyxx//[a-w]/a}"), "xyxx")
        self.assertEqual(self.parse("${xyxx/[a-w]/a}"), "xyxx")
        self.assertEqual(self.parse("${xyxx/*/a}"), "a")
        self.assertEqual(self.parse("${xyxx//*/a}"), "a")
        self.assertEqual(self.parse("${xyxx/y*/a}"), "xa")
        self.assertEqual(self.parse("${xyxx//y*/a}"), "xa")
        self.assertEqual(self.parse("${xyxx//y*/a}"), "xa")
        self.assertEqual(self.parse("${xyxx//y*/}"), "x")
        self.assertEqual(self.parse("${xyxx/y/}"), "xxx")
        self.assertEqual(self.parse("${xyxx//y/}"), "xxx")
        self.assertEqual(self.parse("${xyxx//${xyxx}/${special1}}"), "{{//")

        self.assertEqual(self.parse("${asdf/${${indirect}}/hey}"), "hey")
        self.assertEqual(self.parse("${asdf/${${indirect}}''/hey}"), "qwer")

    def testAdvancedVariabled(self):
        self.assertEqual(self.parse("${unset:->${asdf}}"), ">qwer")
        self.assertEqual(self.parse("""${unset:-">${asdf}"}"""), ">qwer")

    def testIndirectVariables(self):
        self.assertEqual(self.parse("${${indirect}}"), "qwer")
        self.assertEqual(self.parse("${${indirect}:+alternate}"), "alternate")
        self.assertEqual(self.parse("${${asdf}:-default}"), "default")

    def testCommandSubst(self):
        self.assertEqual(self.parse("$(echo,foo,bar)"), "1:foo;2:bar")
        self.assertEqual(self.parse("$(echo,foo bar )"), "1:foo bar ")
        self.assertEqual(self.parse("$(echo,\"foo,bar\" )"), "1:foo,bar ")
        self.assertEqual(self.parse("$(echo,foo \"${asdf} bar\" )"), "1:foo qwer bar ")
        self.assertEqual(self.parse("$(echo,\'foo ${asdf} bar)\' )"), "1:foo ${asdf} bar) ")
        self.assertEqual(self.parse("$(echo,a,${null})"), "1:a;2:")
        self.assertEqual(self.parse("$(echo,a \"${null}\" )"), "1:a  ")

    def testNesting(self):
        self.assertEqual(self.parse("${xyxx%${xyxx%y*}}"), "xyx")

        self.assertEqual(self.parse(
            "$(echo,$(echo,a \"${null:-large ${asdf^^}} word\" ))"),
            "1:1:a large QWER word "
            )
        self.assertEqual(self.parse(
            "$(echo,$(echo,${asdf},no \"${null:-large '${asdf^^}'} word\" ))"),
            "1:1:qwer;2:no large ${asdf^^} word "
            )

        self.assertEqual(self.parse(
            "${null:-${${indirect}} to something};$(echo,$(echo,${asdf},no \"${null:-large '${asdf^^}'} word\" ))"),
            "qwer to something;1:1:qwer;2:no large ${asdf^^} word "
            )

    def testEscaping(self):
        self.assertEqual(self.parse(r"as\df"), r"asdf")
        self.assertEqual(self.parse(r"as\\df"), r"as\df")
        self.assertEqual(self.parse(r"as\'df"), r"as'df")
        self.assertEqual(self.parse("\\${null+foobar}"), "${null+foobar}")
        self.assertEqual(self.parse("${null:-\\}}"), "}")
        self.assertEqual(self.parse("${null:-\\\\}}"), "\}")
        self.assertEqual(self.parse(r"${null:-\\}}"), "\}")
        self.assertEqual(self.parse("$(echo,foo\,bar)"), "1:foo,bar")
        self.assertEqual(self.parse("$(echo,foo\\,bar)"), "1:foo,bar")
        self.assertEqual(self.parse(r"$(echo,foo\\,bar)"), "1:foo\;2:bar")

    def testFails(self):
        self.assertRaises(ParseError, self.parse, "asdf\"")
        self.assertRaises(ParseError, self.parse, "as'df")
        self.assertRaises(ParseError, self.parse, "${asdf")
        self.assertRaises(ParseError, self.parse, "${unknown}")
        self.assertRaises(ParseError, self.parse, "${asdf:")
        self.assertRaises(ParseError, self.parse, "$()")
        self.assertRaises(ParseError, self.parse, "$(unknown)")

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

