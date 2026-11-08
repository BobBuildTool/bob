# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""YAML parsing cache and package (un)pickling."""

from .. import BOB_INPUT_HASH
from ..errors import ParseError
from ..stringparser import IfExpression
from ..utils import binStat
from .core import PathsConfig
from .recipe import Recipe
import copyreg
import hashlib
import pickle
import schema
import sqlite3
import struct
try:
    from yaml import load as yamlLoad, CSafeLoader as YamlSafeLoader
except ImportError:
    from yaml import load as yamlLoad, SafeLoader as YamlSafeLoader


class YamlCache:
    def __if_expression_constructor(loader, node):
        expr = loader.construct_scalar(node)
        return IfExpression(expr)

    YamlSafeLoader.add_constructor(u'!expr', __if_expression_constructor)

    def __init__(self):
        self.__con = None

    def open(self):
        try:
            self.__con = sqlite3.connect(".bob-cache.sqlite3", isolation_level=None)
            self.__cur = self.__con.cursor()
            self.__cur.execute("CREATE TABLE IF NOT EXISTS meta(key PRIMARY KEY, value)")
            self.__cur.execute("CREATE TABLE IF NOT EXISTS yaml(name PRIMARY KEY, stat, digest, data)")

            # check if Bob was changed
            self.__cur.execute("BEGIN")
            self.__cur.execute("SELECT value FROM meta WHERE key='vsn'")
            vsn = self.__cur.fetchone()
            if (vsn is None) or (vsn[0] != BOB_INPUT_HASH):
                # Bob was changed or new workspace -> purge cache
                self.__cur.execute("INSERT OR REPLACE INTO meta VALUES ('vsn', ?)", (BOB_INPUT_HASH,))
                self.__cur.execute("DELETE FROM yaml")
                self.__hot = False
            else:
                # This could work
                self.__hot = True
        except sqlite3.Error as e:
            raise ParseError("Cannot access cache: " + str(e),
                help="You probably executed Bob concurrently in the same workspace. Try again later.")
        self.__files = {}

    def close(self):
        if self.__con is None:
            return

        try:
            self.__cur.execute("END")
            self.__cur.close()
            self.__con.close()
        except sqlite3.Error as e:
            raise ParseError("Cannot commit cache: " + str(e),
                help="You probably executed Bob concurrently in the same workspace. Try again later.")
        h = hashlib.sha1()
        for (name, data) in sorted(self.__files.items()):
            h.update(struct.pack("<I", len(name)))
            h.update(name.encode('utf8'))
            h.update(data)
        self.__digest = h.digest()
        self.__con = None

    def getDigest(self):
        return self.__digest

    def loadYaml(self, name, yamlSchema, default={}, preValidate=lambda x: None):
        try:
            bs = binStat(name) + yamlSchema[1]
            if self.__hot:
                self.__cur.execute("SELECT digest, data FROM yaml WHERE name=? AND stat=?",
                                    (name, bs))
                cached = self.__cur.fetchone()
                if cached is not None:
                    self.__files[name] = cached[0]
                    return pickle.loads(cached[1])

            with open(name, "r", encoding='utf8') as f:
                try:
                    rawData = f.read()
                    data = yamlLoad(rawData, Loader=YamlSafeLoader)
                    digest = hashlib.sha1(rawData.encode('utf8')).digest()
                except Exception as e:
                    raise ParseError("Error while parsing {}: {}".format(name, str(e)))

            if data is None: data = default
            preValidate(data)
            try:
                data = yamlSchema[0].validate(data)
            except schema.SchemaError as e:
                raise ParseError("Error while validating {}: {}".format(name, str(e)))

            self.__files[name] = digest
            self.__cur.execute("INSERT OR REPLACE INTO yaml VALUES (?, ?, ?, ?)",
                (name, bs, digest, pickle.dumps(data)))
        except sqlite3.Error as e:
            raise ParseError("Cannot access cache: " + str(e),
                help="You probably executed Bob concurrently in the same workspace. Try again later.")
        except FileNotFoundError:
            return yamlSchema[0].validate(default)
        except OSError as e:
            raise ParseError("Error loading yaml file: " + str(e))

        return data

    def loadBinary(self, name):
        with open(name, "rb") as f:
            result = f.read()
        self.__files[name] = hashlib.sha1(result).digest()
        return result

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False

def _unpickleRecipe(packageName):
    """Placeholder that is substituted by PackageUnpickler."""
    raise pickle.UnpicklingError("unsupported object")

def _unpicklePathsConfig():
    """Placeholder that is substituted by PackageUnpickler."""
    raise pickle.UnpicklingError("unsupported object")

class PackagePickler(pickle.Pickler):
    # Use the dispatch table to replace Recipe and PathsConfig objects.
    dispatch_table = copyreg.dispatch_table.copy()
    dispatch_table[Recipe] = lambda obj: (_unpickleRecipe, (obj.getPackageName(),))
    dispatch_table[PathsConfig] = lambda obj: (_unpicklePathsConfig, ())

    def __init__(self, file):
        super().__init__(file, -1, fix_imports=False)

class PackageUnpickler(pickle.Unpickler):
    def __init__(self, file, recipeGetter, plugins, pathsConfig):
        super().__init__(file)
        self.__recipeGetter = recipeGetter
        self.__plugins = plugins
        self.__pathsConfig = pathsConfig

    def find_class(self, module, name):
        if module == __name__ and name == "_unpickleRecipe":
            return self.__recipeGetter
        elif module == __name__ and name == "_unpicklePathsConfig":
            return lambda: self.__pathsConfig
        elif module.startswith("__bob_plugin_"):
            return getattr(self.__plugins[module], name)
        else:
            return super().find_class(module, name)
