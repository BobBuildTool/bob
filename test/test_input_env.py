# Bob build tool
# Copyright (C) 2017  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from unittest import TestCase
import schema

from bob.input import Env, VarDefineValidator

class TestEnv(TestCase):

    def testDerive(self):
        e1 = Env()
        e2 = e1.derive()

        e1.get('bar')
        e2.get('foo')

        self.assertEqual(e1.touchedKeys(), set(['foo', 'bar']))
        self.assertEqual(e2.touchedKeys(), set(['foo', 'bar']))

    def testDeriveReset(self):
        e1 = Env()
        e2 = e1.derive()
        e2.touchReset()

        e1.get('bar')
        e2.get('foo')

        self.assertEqual(e1.touchedKeys(), set(['foo', 'bar']))
        self.assertEqual(e2.touchedKeys(), set(['foo']))

    def testDeriveResetTwo(self):
        e1 = Env()
        e2 = e1.derive()
        e2.touchReset()
        e3 = e2.derive()
        e3.touchReset()

        e1.get('bar')
        e2.get('foo')
        e3.get('baz')

        self.assertEqual(e1.touchedKeys(), set(['foo', 'bar', 'baz']))
        self.assertEqual(e2.touchedKeys(), set(['foo', 'baz']))
        self.assertEqual(e3.touchedKeys(), set(['baz']))

    def testDeriveResetSplit(self):
        e1 = Env()
        e2 = e1.derive()
        e2.touchReset()
        e3 = e1.derive()
        e3.touchReset()

        e1.get('bar')
        e2.get('foo')
        e3.get('baz')

        self.assertEqual(e1.touchedKeys(), set(['foo', 'bar', 'baz']))
        self.assertEqual(e2.touchedKeys(), set(['foo']))
        self.assertEqual(e3.touchedKeys(), set(['baz']))

    def testTouch(self):
        e1 = Env()
        e1.touch(['foo'])
        self.assertEqual(e1.touchedKeys(), set(['foo']))


class TestVarDefineValidator(TestCase):
    def setUp(self):
        self.v = VarDefineValidator("foo")

    def testValid(self):
        self.assertEqual(self.v.validate({"FOO": "bar"}), {"FOO": "bar"})

    def testWrongTypes(self):
        self.assertRaises(schema.SchemaError, self.v.validate, "boom")
        self.assertRaises(schema.SchemaError, self.v.validate, {1 : "bar"})
        self.assertRaises(schema.SchemaError, self.v.validate, {"foo" : True})

    def testWrongNames(self):
        self.assertRaises(schema.SchemaError, self.v.validate, {"0abc" : "bar"})
        self.assertRaises(schema.SchemaError, self.v.validate, {"BOB_FOO" : "bar"})

