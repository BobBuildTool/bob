# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""SCM instantiation and checkout assertions."""

from ..errors import ParseError
from ..scm import getScm, SYNTHETIC_SCM_PROPS
import hashlib
import schema


def Scm(spec, env, overrides, recipeSet):
    def _substitute(k, v):
        if isinstance(v, str) and k not in ('__source', 'recipe'):
            return env.substitute(v, "checkoutSCM::"+k)
        elif isinstance(v, dict):
            return { k1 : _substitute(k+"::"+k1, v1) for (k1, v1) in v.items() }
        else:
            return v

    # resolve with environment
    spec = { k : _substitute(k,v)
        for (k, v) in spec.items() }

    # apply overrides before creating scm instances. It's possible to switch the Scm type with an override..
    matchedOverrides = []
    for override in overrides:
        matched, spec = override.mangle(spec, env)
        if matched:
            matchedOverrides.append(override)

    # check schema again if any SCM override matched
    if matchedOverrides:
        try:
            recipeSet.SCM_SCHEMA.validate({ k:v for k,v in spec.items()
                if k not in SYNTHETIC_SCM_PROPS })
        except schema.SchemaError as e:
            raise ParseError("Error validating SCM after applying scmOverrides: {}".format(str(e)))

    # apply scmDefaults
    for k, v in recipeSet.scmDefaults().get(spec['scm'], {}).items():
        spec.setdefault(k, v)

    # create scm instance
    return getScm(spec, matchedOverrides, recipeSet)

class CheckoutAssert:
    __slots__ = ('__source', '__file', '__digestSHA1', '__start', '__end')

    SCHEMA = schema.Schema({
        'file' : str,
        'digestSHA1' : str,
        schema.Optional('start') : schema.Or(str, int),
        schema.Optional('end') : schema.Or(str, int)
    })

    def __init__(self, spec, env=None):
        if env is not None:
            spec = {k: ( env.substitute(v, "checkoutAssert::"+ k) if isinstance(v, str) else v)
                    for (k, v) in spec.items()}
        self.__source = spec['__source']
        self.__file = spec['file']
        self.__digestSHA1 = spec['digestSHA1']
        try:
            self.__start = int(spec.get('start', 1))
            self.__end = int(spec.get('end', 0xffffffff))
        except ValueError as e:
            raise ParseError("checkoutAssert: invalid start/end: " + str(e))
        if self.__start < 1:
            raise ParseError("CheckoutAssert: First line must be greater than zero")
        if self.__end < 1:
            raise ParseError("CheckoutAssert: Last line must be greater than zero")

    def getProperties(self):
        return {
            '__source' : self.__source,
            'file' : self.__file,
            'digestSHA1' : self.__digestSHA1,
            'start' : self.__start,
            'end' : self.__end,
        }

    def getSource(self):
        return self.__source

    async def invoke(self, invoker):
        h = hashlib.sha1()
        i = 0
        try:
            with open(invoker.joinPath(self.__file), "rb") as f:
                for line in f:
                    i += 1
                    if i < self.__start: continue
                    if (i == self.__start) or (i <= self.__end): h.update(line)
                    if i > self.__end: break
            d = h.digest().hex()
            if d != self.__digestSHA1:
                invoker.fail(self.__file, "digest did not match! expected:", self.__digestSHA1, "got:", d)
        except OSError as e:
            invoker.fail(str(e))

    def asDigestScript(self):
        return self.__file + " " + self.__digestSHA1 + " " + str(self.__start) + " " + str(self.__end)
