# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Small helpers shared by the recipe parser and the package model."""

from ..languages import ScriptLanguage
from ..utils import joinScripts
from itertools import chain
import hashlib
import fnmatch
import os, os.path


def isPrefixPath(p1, p2):
    """Check if the initial elements of ``p2`` equal ``p1``.

    :return: True if ``p2`` is a subdirectory or file inside ``p1``, otherwise
             False.
    """
    p1 = os.path.normcase(os.path.normpath(p1)).split(os.sep)
    if p1 == ["."]: p1 = []
    p2 = os.path.normcase(os.path.normpath(p2)).split(os.sep)
    if p2 == ["."]: p2 = []

    if len(p1) > len(p2):
        return False

    return p2[:len(p1)] == p1

def __maybeGlob(pred):
    if pred.startswith("!"):
        pred = pred[1:]
        if any(i in pred for i in '*?[]'):
            return lambda prev, elem: False if fnmatch.fnmatchcase(elem, pred) else prev
        else:
            return lambda prev, elem: False if elem == pred else prev
    else:
        if any(i in pred for i in '*?[]'):
            return lambda prev, elem: True if fnmatch.fnmatchcase(elem, pred) else prev
        else:
            return lambda prev, elem: True if elem == pred else prev

def maybeGlob(pattern):
    if isinstance(pattern, list):
        return [ __maybeGlob(p) for p in pattern ]
    else:
        return None

class __uidGen:
    def __init__(self):
        self.cur = 0
    def get(self):
        self.cur += 1
        return self.cur

uidGen = __uidGen().get

class DigestHasher:
    def __init__(self):
        self.__recipes = bytearray()
        self.__host = bytearray()

    def update(self, real):
        """Add bytes to recipe-internal part of digest."""
        self.__recipes.extend(real)

    def fingerprint(self, imag):
        """Add bytes of fingerprint to host part of digest."""
        self.__host.extend(imag)

    def digest(self):
        """Calculate final digest value.

        If no host fingerprints were added only the recipe-internal digest is
        emitted. Otherwise the fingerprint digest is appended. This keeps the
        calculation backwards compatible (Bob <0.15).
        """
        if self.__host:
            return hashlib.sha1(self.__recipes).digest() + \
                   hashlib.sha1(self.__host).digest()
        else:
            return hashlib.sha1(self.__recipes).digest()

    @staticmethod
    def sliceRecipes(digest):
        """Extract recipe-internal digest part."""
        return digest[:20]

    @staticmethod
    def sliceHost(digest):
        """Extract host fingerprint digest part (if any)."""
        return digest[20:]

def fetchFingerprintScripts(recipe):
    return {
        ScriptLanguage.BASH : recipe.get("fingerprintScriptBash",
            recipe.get("fingerprintScript")),
        ScriptLanguage.PWSH : recipe.get("fingerprintScriptPwsh",
            recipe.get("fingerprintScript")),
        ScriptLanguage.PYTHON : recipe.get("fingerprintScriptPython",
            recipe.get("fingerprintScript")),
    }

def fetchScripts(recipe, prefix, resolveBash, resolvePwsh, resolvePython):
    return {
        ScriptLanguage.BASH : (
            resolveBash(recipe.get(prefix + "SetupBash", recipe.get(prefix + "Setup")),
                        prefix + "Setup[Bash]"),
            resolveBash(recipe.get(prefix + "ScriptBash", recipe.get(prefix + "Script")),
                        prefix + "Script[Bash]"),
            resolveBash(recipe.get(prefix + "FinalizeBash", recipe.get(prefix + "Finalize")),
                        prefix + "Finalize[Bash]"),
        ),
        ScriptLanguage.PWSH : (
            resolvePwsh(recipe.get(prefix + "SetupPwsh", recipe.get(prefix + "Setup")),
                        prefix + "Setup[Pwsh]"),
            resolvePwsh(recipe.get(prefix + "ScriptPwsh", recipe.get(prefix + "Script")),
                        prefix + "Script[Pwsh]"),
            resolvePwsh(recipe.get(prefix + "FinalizePwsh", recipe.get(prefix + "Finalize")),
                        prefix + "Finalize[Pwsh]"),
        ),
        ScriptLanguage.PYTHON : (
            resolvePython(recipe.get(prefix + "SetupPython", recipe.get(prefix + "Setup")),
                          prefix + "Setup[Python]"),
            resolvePython(recipe.get(prefix + "ScriptPython", recipe.get(prefix + "Script")),
                          prefix + "Script[Python]"),
            resolvePython(recipe.get(prefix + "FinalizePython", recipe.get(prefix + "Finalize")),
                          prefix + "Finalize[Python]"),
        ),
    }

def mergeScripts(fragments, glue):
    """Join all scripts of the recipe and its classes.

    The result is a tuple with (setupScript, mainScript, digestScript,
    includedFiles). Note that the mainScript contains all "normal" scripts and
    all "Finalize" scripts in reverse order.
    """

    return (
        # The "Setup" scripts
        joinScripts((f[0][0] for f in fragments), glue),
        # The "normal" scripts and the "Finalize" scripts
        joinScripts(chain((f[1][0] for f in fragments),
                          (f[2][0] for f in reversed(fragments))), glue),
        # All the digest scripts
        joinScripts(
            ( joinScripts((f[0][1] for f in fragments), "\n"),
              joinScripts((f[1][1] for f in fragments), "\n"),
              joinScripts((f[2][1] for f in fragments), "\n"),
            ), "\n"),
        { name : content for name, content in
                         chain.from_iterable(chain(f[0][2].items(), f[1][2].items()) for f in fragments)
        },
    )
