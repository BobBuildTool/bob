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

from bob.input import StringParser
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
