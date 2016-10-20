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

from .scm import Scm, ScmOverride
from .cvs import CvsScm
from .git import GitScm, GitAudit
from .svn import SvnScm, SvnAudit
from .url import UrlScm, UrlAudit
import os.path
import schema

def auditFromDir(dir):
    if os.path.isdir(os.path.join(dir, ".git")):
        return GitAudit.fromDir(dir, ".")
    elif os.path.isdir(os.path.join(dir, ".svn")):
        return SvnAudit.fromDir(dir, ".")
    else:
        return None

def auditFromData(data):
    typ = data.get("type")
    if typ == "git":
        scm = GitAudit
    elif typ == "url":
        scm = UrlAudit
    elif typ == "svn":
        scm = SvnAudit
    else:
        from ..errors import ParseError
        raise ParseError("Cannot handle SCM: " + str(typ))

    try:
        data = scm.SCHEMA.validate(data)
        return scm.fromData(data)
    except schema.SchemaError as e:
        from ..errors import ParseError
        raise ParseError("Error while validating audit: {} {}".format(str(e), str(data)))

