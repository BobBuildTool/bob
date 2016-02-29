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

from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest import TestCase
import os, stat

from bob.utils import joinScripts, removePath, emptyDirectory
from bob.errors import BuildError

class TestJoinScripts(TestCase):

    def testEmpty(self):
        assert joinScripts([]) == None

    def testSingle(self):
        assert joinScripts(["asdf"]) == "asdf"
        assert joinScripts([None]) == None

    def testDual(self):
        s = joinScripts(["asdf", "qwer"]).splitlines()
        assert "asdf" in s
        assert "qwer" in s
        assert s.index("asdf") < s.index("qwer")

        assert joinScripts(["asdf", None]) == "asdf"
        assert joinScripts([None, "asdf"]) == "asdf"

        assert joinScripts([None, None]) == None

class TestRemove(TestCase):

    def testFile(self):
        with TemporaryDirectory() as tmp:
            fn = os.path.join(tmp, "file")
            with open(fn, "w") as f:
                f.write("data")
            removePath(fn)
            assert not os.path.exists(fn)

    def testDir(self):
        with TemporaryDirectory() as tmp:
            d = os.path.join(tmp, "dir")
            os.mkdir(d)
            with open(os.path.join(d, "file"), "w") as f:
                f.write("data")

            removePath(d)
            assert not os.path.exists(d)

    def testPermission(self):
        with TemporaryDirectory() as tmp:
            d = os.path.join(tmp, "dir")
            os.mkdir(d)
            with open(os.path.join(d, "file"), "w") as f:
                f.write("data")

            os.chmod(d, stat.S_IRUSR | stat.S_IXUSR)
            self.assertRaises(BuildError, removePath, tmp)
            os.chmod(d, stat.S_IRWXU)

class TestEmpty(TestCase):

    def testFile(self):
        with TemporaryDirectory() as tmp:
            fn = os.path.join(tmp, "file")
            with open(fn, "w") as f:
                f.write("data")

            emptyDirectory(tmp)
            assert os.path.exists(tmp)
            assert not os.path.exists(fn)

    def testDir(self):
        with TemporaryDirectory() as tmp:
            d = os.path.join(tmp, "dir")
            os.mkdir(d)
            with open(os.path.join(d, "file"), "w") as f:
                f.write("data")

            emptyDirectory(tmp)
            assert os.path.exists(tmp)
            assert not os.path.exists(d)

    def testPermission(self):
        with TemporaryDirectory() as tmp:
            d = os.path.join(tmp, "dir")
            os.mkdir(d)
            with open(os.path.join(d, "file"), "w") as f:
                f.write("data")

            os.chmod(d, stat.S_IRUSR | stat.S_IXUSR)
            self.assertRaises(BuildError, emptyDirectory, tmp)
            os.chmod(d, stat.S_IRWXU)

