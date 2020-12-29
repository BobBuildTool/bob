# Bob build tool
# Copyright (C) 2017  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ..audit import Audit
from ..errors import BobError
from ..utils import binStat, asHexStr, infixBinaryOp
import argparse
import gzip
import json
import os, os.path
import pickle
import pyparsing
import re
import sqlite3
import sys
import tarfile

# need to enable this for nested expression parsing performance
pyparsing.ParserElement.enablePackrat()

class ArchiveScanner:
    CUR_VERSION = 2

    def __init__(self):
        self.__dirSchema = re.compile(r'[0-9a-zA-Z]{2}')
        self.__archiveSchema = re.compile(r'[0-9a-zA-Z]{36,}-1.tgz')
        self.__db = None
        self.__cleanup = False

    def __enter__(self):
        try:
            self.__con = sqlite3.connect(".bob-archive.sqlite3", isolation_level=None)
            self.__db = self.__con.cursor()
            self.__db.execute("""\
                CREATE TABLE IF NOT EXISTS meta(
                    key TEXT PRIMARY KEY NOT NULL,
                    value
                )""")
            self.__db.execute("SELECT value FROM meta WHERE key='vsn'")
            vsn = self.__db.fetchone()
            if vsn is None:
                self.__db.executescript("""
                    CREATE TABLE files(
                        bid BLOB PRIMARY KEY NOT NULL,
                        stat BLOB,
                        vars BLOB
                    );
                    CREATE TABLE refs(
                        bid BLOB NOT NULL,
                        ref BLOB NOT NULL,
                        PRIMARY KEY (bid, ref)
                    );
                    """)
                self.__db.execute("INSERT INTO meta VALUES ('vsn', ?)", (self.CUR_VERSION,))
            elif vsn[0] != self.CUR_VERSION:
                raise BobError("Archive database was created by an incompatible version of Bob!",
                    help="Delete '.bob-archive.sqlite3' and run again to re-index.")
        except sqlite3.Error as e:
            raise BobError("Cannot open cache: " + str(e))
        return self

    def __exit__(self, *exc):
        try:
            if self.__cleanup:
                # prune references where files have been removed
                self.__db.execute("""\
                    DELETE FROM refs WHERE bid NOT IN (
                        SELECT bid FROM files
                    )""")
            self.__db.close()
            self.__con.close()
        except sqlite3.Error as e:
            raise BobError("Cannot close cache: " + str(e))
        self.__db = None
        return False

    def scan(self, verbose):
        try:
            found = False
            self.__db.execute("BEGIN")
            for l1 in os.listdir("."):
                if not self.__dirSchema.fullmatch(l1): continue
                for l2 in os.listdir(l1):
                    if not self.__dirSchema.fullmatch(l2): continue
                    l2 = os.path.join(l1, l2)
                    for l3 in os.listdir(l2):
                        m = self.__archiveSchema.fullmatch(l3)
                        if not m: continue
                        found = True
                        self.__scan(os.path.join(l2, l3), verbose)
        except OSError as e:
            raise BobError("Error scanning archive: " + str(e))
        finally:
            self.__db.execute("END")
            if verbose and not found:
                print("Your archive seems to be empty. "
                      "Are you running 'bob archive' from within the correct directory?",
                      file=sys.stderr)
            return found

    def __scan(self, fileName, verbose):
        try:
            st = binStat(fileName)
            bidHex, sep, suffix = fileName.partition("-")
            bid = bytes.fromhex(bidHex[0:2] + bidHex[3:5] + bidHex[6:])

            # Validate entry in caching db. Delete entry if stat has changed.
            # The database will clean the 'refs' table automatically.
            self.__db.execute("SELECT stat FROM files WHERE bid=?",
                                (bid,))
            cachedStat = self.__db.fetchone()
            if cachedStat is not None:
                if cachedStat[0] == st: return
                self.__db.execute("DELETE FROM files WHERE bid=?",
                    (bid,))

            # read audit trail
            if verbose: print("scan", fileName)
            with tarfile.open(fileName, errorlevel=1) as tar:
                # validate
                if tar.pax_headers.get('bob-archive-vsn') != "1":
                    print("Not a Bob archive:", fileName, "Ignored!")
                    return

                # find audit trail
                f = tar.next()
                while f:
                    if f.name == "meta/audit.json.gz": break
                    f = tar.next()
                else:
                    raise Error("Missing audit trail!")

                # read audit trail
                auditJsonGz = tar.extractfile(f)
                auditJson = gzip.GzipFile(fileobj=auditJsonGz)
                audit = Audit.fromByteStream(auditJson, fileName)

            # import data
            artifact = audit.getArtifact()
            vrs = pickle.dumps({
                'meta' : artifact.getMetaData(),
                'build' : artifact.getBuildInfo(),
                'metaEnv' : artifact.getMetaEnv(),
            })
            self.__db.execute("INSERT INTO files VALUES (?, ?, ?)",
                (bid, st, vrs))
            self.__db.executemany("INSERT OR IGNORE INTO refs VALUES (?, ?)",
                [ (bid, r) for r in audit.getReferencedBuildIds() ])
        except tarfile.TarError as e:
            raise BobError("Cannot read {}: {}".format(fileName, str(e)))
        except OSError as e:
            raise BobError(str(e))

    def remove(self, bid):
        self.__cleanup = True
        self.__db.execute("DELETE FROM files WHERE bid=?",
            (bid,))

    def getBuildIds(self):
        self.__db.execute("SELECT bid FROM files")
        return [ r[0] for r in self.__db.fetchall() ]

    def getReferencedBuildIds(self, bid):
        self.__db.execute("SELECT ref FROM refs WHERE bid=?",
            (bid,))
        return [ r[0] for r in self.__db.fetchall() ]

    def getVars(self, bid):
        self.__db.execute("SELECT vars FROM files WHERE bid=?",
            (bid,))
        v = self.__db.fetchone()
        if v:
            return pickle.loads(v[0])
        else:
            return {}


class Base:
    def __init__(self, s, loc):
        self.__s = s
        self.__loc = loc

    def barf(self, msg, help=None):
        h = "Offending query: " + self.__s + "\n" + (" " * (self.__loc + 17)) + \
            "^.-- Error location"
        if help is not None:
            h += "\n" + help
        raise BobError("Bad query: "+msg+"!", help=h)

class NotPredicate(Base):
    def __init__(self, s, loc, toks):
        super().__init__(s, loc)
        self.arg = toks[0][1]

    def __repr__(self):
        return "NotPredicate({})".format(self.arg)

    def evalBool(self, data):
        return not self.arg.evalBool(data)

    def evalString(self, data):
        self.barf("operator in string context")

class AndPredicate(Base):
    def __init__(self, s, loc, toks):
        super().__init__(s, loc)
        self.left = toks[0]
        self.right = toks[2]

    def __repr__(self):
        return "AndPredicate({}, {})".format(self.left, self.right)

    def evalBool(self, data):
        return self.left.evalBool(data) and self.right.evalBool(data)

    def evalString(self, data):
        self.barf("operator in string context")

class OrPredicate(Base):
    def __init__(self, s, loc, toks):
        super().__init__(s, loc)
        self.left = toks[0]
        self.right = toks[2]

    def __repr__(self):
        return "OrPredicate({}, {})".format(self.left, self.right)

    def evalBool(self, data):
        return self.left.evalBool(data) or self.right.evalBool(data)

    def evalString(self, data):
        self.barf("operator in string context")

class ComparePredicate(Base):
    def __init__(self, s, loc, toks):
        super().__init__(s, loc)
        self.left = toks[0]
        self.right = toks[2]
        op = toks[1]
        if op == '<':
            self.op = lambda l, r: l < r
        elif op == '>':
            self.op = lambda l, r: l > r
        elif op == '<=':
            self.op = lambda l, r: l <= r
        elif op == '>=':
            self.op = lambda l, r: l >= r
        elif op == '==':
            self.op = lambda l, r: l == r
        elif op == '!=':
            self.op = lambda l, r: l != r
        else:
            assert False, op

    def __repr__(self):
        return "ComparePredicate({}, {})".format(self.left, self.right)

    def evalBool(self, data):
        try:
            return self.op(self.left.evalString(data), self.right.evalString(data))
        except:
            self.barf("predicate not supported between operands",
                      "Most probably one of the sides of the expression referenced a non-existing field. Only '==' and '!=' are supported in this case.")

    def evalString(self, data):
        self.barf("operator in string context")

class StringLiteral(Base):
    def __init__(self, s, loc, toks):
        super().__init__(s, loc)
        self.literal = toks[0]

    def evalBool(self, data):
        self.barf("string in boolean context")

    def evalString(self, data):
        return self.literal

class VarReference(Base):
    def __init__(self, s, loc, toks):
        super().__init__(s, loc)
        self.path = toks[0].split(".")

    def evalBool(self, data):
        self.barf("field reference in boolean context")

    def evalString(self, data):
        try:
            for i in self.path:
                data = data[i]
        except:
            return None
        if not isinstance(data, str):
            self.barf("invalid field reference")
        return data

class RetainExpression(Base):
    def __init__(self, s, loc, toks):
        super().__init__(s, loc)
        self.expr = toks[0]
        self.limit = None if len(toks) < 2 else int(toks[1])
        if self.limit is not None and self.limit <= 0:
            self.barf("LIMIT takes a number greater or equal to one!")
        self.sortBy = VarReference(None, None, ["build.date"]) \
            if len(toks) < 3 else toks[2]
        if len(toks) >= 4 and (toks[3] == "ASC"):
            def cmpItem(existing, new):
                if new is None: return False
                return existing is None or existing >= new
        else:
            def cmpItem(existing, new):
                if new is None: return False
                return existing is None or existing <= new
        self.cmpItem = cmpItem
        self.retained = set()
        self.queue = []

    def evaluate(self, bid, data):
        if bid in self.retained: return
        if not self.expr.evalBool(data): return
        self.retained.add(bid)

        # limit results based on self.sortBy ordered according to self.cmpItem
        if self.limit is None: return
        new = self.sortBy.evalString(data)
        i = 0
        while i < len(self.queue):
            if self.cmpItem(self.queue[i][1], new): break
            i += 1
        self.queue.insert(i, (bid, new))
        while len(self.queue) > self.limit:
            victim,_ = self.queue.pop()
            self.retained.remove(victim)

    def getRetained(self):
        return self.retained

# meta.package == "root" && build.date > "2017-06-19" LIMIT 5 ORDER BY build.date ASC
def query(scanner, expressions):
    varReference = pyparsing.Word(pyparsing.alphanums+'._-')
    varReference.setParseAction(lambda s, loc, toks: VarReference(s, loc, toks))

    stringLiteral = pyparsing.QuotedString('"', '\\')
    stringLiteral.setParseAction(lambda s, loc, toks: StringLiteral(s, loc, toks))

    selectExpr = pyparsing.infixNotation(
        stringLiteral | varReference,
        [
            ('!',  1, pyparsing.opAssoc.RIGHT, lambda s, loc, toks: NotPredicate(s, loc, toks)),
            ('<',  2, pyparsing.opAssoc.LEFT,  infixBinaryOp(ComparePredicate)),
            ('<=', 2, pyparsing.opAssoc.LEFT,  infixBinaryOp(ComparePredicate)),
            ('>',  2, pyparsing.opAssoc.LEFT,  infixBinaryOp(ComparePredicate)),
            ('>=', 2, pyparsing.opAssoc.LEFT,  infixBinaryOp(ComparePredicate)),
            ('==', 2, pyparsing.opAssoc.LEFT,  infixBinaryOp(ComparePredicate)),
            ('!=', 2, pyparsing.opAssoc.LEFT,  infixBinaryOp(ComparePredicate)),
            ('&&', 2, pyparsing.opAssoc.LEFT,  infixBinaryOp(AndPredicate)),
            ('||', 2, pyparsing.opAssoc.LEFT,  infixBinaryOp(OrPredicate))
        ])

    expr = selectExpr + pyparsing.Optional(
        pyparsing.CaselessKeyword("LIMIT").suppress() -
            pyparsing.Word(pyparsing.nums) +
        pyparsing.Optional(pyparsing.CaselessKeyword("ORDER").suppress() -
                           pyparsing.CaselessKeyword("BY").suppress() -
                           varReference +
        pyparsing.Optional(pyparsing.CaselessKeyword("ASC") |
                           pyparsing.CaselessKeyword("DESC"))))
    expr.setParseAction(lambda s, loc, toks: RetainExpression(s, loc, toks))

    try:
        retainExpressions = [ expr.parseString(e, True)[0] for e in expressions ]
    except pyparsing.ParseBaseException as e:
        raise BobError("Invalid retention expression: " + str(e))

    for bid in scanner.getBuildIds():
        data = scanner.getVars(bid)
        for expr in retainExpressions:
            expr.evaluate(bid, data)

    retained = set()
    for expr in retainExpressions:
        retained.update(expr.getRetained())

    return retained


def doArchiveScan(argv):
    parser = argparse.ArgumentParser(prog="bob archive scan")
    parser.add_argument("-v", "--verbose", action='store_true',
        help="Verbose operation")
    parser.add_argument("-f", "--fail", action='store_true',
        help="Return a non-zero error code in case of errors")
    args = parser.parse_args(argv)

    scanner = ArchiveScanner()
    with scanner:
        if not scanner.scan(args.verbose) and args.fail:
            sys.exit(1)


# meta.package == "root" && build.date > "2017-06-19" LIMIT 5 ORDER BY build.date ASC
def doArchiveClean(argv):
    parser = argparse.ArgumentParser(prog="bob archive clean")
    parser.add_argument('expression', nargs='+',
        help="Expression of artifacts that shall be kept")
    parser.add_argument('--dry-run', default=False, action='store_true',
        help="Don't delete, just print what would be deleted")
    parser.add_argument('-n', dest='noscan', action='store_true',
        help="Skip scanning for new artifacts")
    parser.add_argument("-v", "--verbose", action='store_true',
        help="Verbose operation")
    parser.add_argument("-f", "--fail", action='store_true',
        help="Return a non-zero error code in case of errors")
    args = parser.parse_args(argv)

    scanner = ArchiveScanner()
    with scanner:
        if not args.noscan:
            if not scanner.scan(args.verbose) and args.fail:
                sys.exit(1)

        # First pass: determine all directly retained artifacts
        retained = query(scanner, args.expression)

        # Second pass: determine all transitively retained artifacts
        todo = set()
        for bid in retained:
            todo.update(scanner.getReferencedBuildIds(bid))
        while todo:
            n = todo.pop()
            if n in retained: continue
            retained.add(n)
            todo.update(scanner.getReferencedBuildIds(n))

        # Third pass: remove everything that is *not* retained
        for bid in scanner.getBuildIds():
            if bid in retained: continue
            victim = asHexStr(bid)
            victim = os.path.join(victim[0:2], victim[2:4], victim[4:] + "-1.tgz")
            if args.dry_run:
                print(victim)
            else:
                try:
                    if args.verbose:
                        print("rm", victim)
                    os.unlink(victim)
                except FileNotFoundError:
                    pass
                except OSError as e:
                    raise BobError("Cannot remove {}: {}".format(victim, str(e)))
                scanner.remove(bid)

def doArchiveFind(argv):
    parser = argparse.ArgumentParser(prog="bob archive find")
    parser.add_argument('expression', nargs='+',
        help="Expression that artifacts need to match")
    parser.add_argument('-n', dest='noscan', action='store_true',
        help="Skip scanning for new artifacts")
    parser.add_argument("-v", "--verbose", action='store_true',
        help="Verbose operation")
    parser.add_argument("-f", "--fail", action='store_true',
        help="Return a non-zero error code in case of errors")
    args = parser.parse_args(argv)

    scanner = ArchiveScanner()
    with scanner:
        if not args.noscan:
            if not scanner.scan(args.verbose) and args.fail:
                sys.exit(1)

        # First pass: determine all directly retained artifacts
        retained = query(scanner, args.expression)

    for bid in sorted(retained):
        bid = asHexStr(bid)
        print(os.path.join(bid[0:2], bid[2:4], bid[4:] + "-1.tgz"))

availableArchiveCmds = {
    "scan" : (doArchiveScan, "Scan archive for new artifacts"),
    "clean" : (doArchiveClean, "Clean archive from unneeded artifacts"),
    "find" : (doArchiveFind, "Print matching artifacts"),
}

def doArchive(argv, bobRoot):
    subHelp = "\n          ... ".join(sorted(
        [ "{:8} {}".format(c, d[1]) for (c, d) in availableArchiveCmds.items() ]))
    parser = argparse.ArgumentParser(prog="bob archive",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""Manage binary artifacts archive. The following subcommands are available:

  bob archive {}
""".format(subHelp))
    parser.add_argument('subcommand', help="Subcommand")
    parser.add_argument('args', nargs=argparse.REMAINDER,
                        help="Arguments for subcommand")

    args = parser.parse_args(argv)

    if args.subcommand in availableArchiveCmds:
        availableArchiveCmds[args.subcommand][0](args.args)
    else:
        parser.error("Unknown subcommand '{}'".format(args.subcommand))

