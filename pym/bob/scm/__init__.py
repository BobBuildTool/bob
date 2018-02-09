# Bob build tool
# Copyright (C) 2016  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

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

