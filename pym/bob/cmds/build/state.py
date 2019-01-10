# Bob build tool
# Copyright (C) 2016-2018  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ...errors import BobError
import os
import sqlite3


class DevelopDirOracle:
    """
    Calculate directory names for develop mode.

    If an external "persister" is used we just cache the calculated values. We
    don't know it's behaviour and have to re-calculate everything from scratch
    to be on the safe side.

    The internal algorithm creates a separate directory for every recipe and
    step variant. Only identical steps of the same recipe are put into the
    same directory. In contrast to the releaseNamePersister() identical steps
    of different recipes are put into distinct directories. If the recipes are
    changed we keep existing mappings that still match the base directory.

    Populating the database is done by traversing all packages and invoking the
    name formatter for the visited packages. In case of the external persister
    the result is directly cached. For the internal algorithm it has to be done
    in two passes. The first pass collects all packages and their base
    directories, possibly re-using old matches if possible. The second pass
    assigns the final directory names to all other entries and writes them into
    the database.
    """

    def __init__(self, formatter, externalPersister):
        self.__formatter = formatter
        self.__externalPersister = externalPersister(formatter) \
            if externalPersister is not None else None
        self.__dirs = {}
        self.__known = {}
        self.__visited = set()
        self.__ready = False

    def __fmt(self, step, props):
        key = step.getPackage().getRecipe().getName().encode("utf8") + step.getVariantId()

        # Always look into the database first. We almost always need it.
        self.__db.execute("SELECT dir FROM dirs WHERE key=?", (key,))
        path = self.__db.fetchone()
        path = path[0] if path is not None else None

        # If we're ready we just interrogate the database.
        if self.__ready:
            assert path is not None, "{} missing".format(key)
            return path

        # Make sure to process each key only once. A key might map to several
        # directories. We have to make sure to take only the first one, though.
        if key in self.__visited: return
        self.__visited.add(key)

        # If an external persister is used we just call it and save the result.
        if self.__externalPersister is not None:
            self.__known[key] = self.__externalPersister(step, props)
            return

        # Try to find directory in database. If we find some the prefix has to
        # match. Otherwise schedule for number assignment in next round by
        # __writeBack(). The final path is then not decided yet.
        baseDir = self.__formatter(step, props)
        if (path is not None) and path.startswith(baseDir):
            self.__known[key] = path
        else:
            self.__dirs.setdefault(baseDir, []).append(key)

    def __touch(self, package, done):
        """Run through all dependencies and invoke name formatter.

        Traversal is done on package level to gather all reachable packages of
        the query language.
        """
        key = package._getId()
        if key in done: return
        done.add(key)

        # Traverse direct package dependencies only to keep the recipe order.
        # Because we start from the root we are guaranteed to see all packages.
        for d in package.getDirectDepSteps():
            self.__touch(d.getPackage(), done)

        # Calculate the paths of all steps
        package.getPackageStep().getWorkspacePath()
        package.getBuildStep().getWorkspacePath()
        package.getCheckoutStep().getWorkspacePath()

    def __writeBack(self):
        """Write calculated directories into database.

        We have to write known entries and calculate new sub-directory numbers
        for new entries.
        """
        # clear all mappings
        self.__db.execute("DELETE FROM dirs")

        # write kept entries
        self.__db.executemany("INSERT INTO dirs VALUES (?, ?)", self.__known.items())
        knownDirs = set(self.__known.values())

        # Add trailing number to new entries. Make sure they don't collide with
        # kept entries...
        for baseDir,keys in self.__dirs.items():
            num = 1
            for key in keys:
                while True:
                    path = os.path.join(baseDir, str(num))
                    num += 1
                    if path in knownDirs: continue
                    self.__db.execute("INSERT INTO dirs VALUES (?, ?)", (key, path))
                    break

        # Clear intermediate variables to save memory.
        self.__dirs = {}
        self.__known = {}
        self.__visited = set()

    def __openAndRefresh(self, cacheKey, rootPackage):
        self.__db = db = sqlite3.connect(".bob-dev-dirs.sqlite3", isolation_level=None).cursor()
        db.execute("CREATE TABLE IF NOT EXISTS meta(key PRIMARY KEY, value)")
        db.execute("CREATE TABLE IF NOT EXISTS dirs(key PRIMARY KEY, dir)")

        # Check if recipes were changed.
        db.execute("BEGIN")
        db.execute("SELECT value FROM meta WHERE key='vsn'")
        vsn = db.fetchone()
        if (vsn is None) or (vsn[0] != cacheKey):
            self.__touch(rootPackage, set())
            self.__writeBack()
            db.execute("INSERT OR REPLACE INTO meta VALUES ('vsn', ?)", (cacheKey,))
            # Commit and start new read-only transaction
            db.execute("END")
            db.execute("BEGIN")

    def prime(self, packages):
        try:
            self.__openAndRefresh(packages.getCacheKey(),
                packages.getRootPackage())
        except sqlite3.Error as e:
            raise BobError("Cannot save directory mapping: " + str(e))
        self.__ready = True

    def getFormatter(self):
        return self.__fmt

