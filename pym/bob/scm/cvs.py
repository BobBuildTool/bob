# Bob build tool
# Copyright (C) 2016  Stefan Reuther
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

from ..utils import hashString
import re
import schema

class CvsScm:

    SCHEMA = schema.Schema({
        'scm' : 'cvs',
        'cvsroot' : str,
        'module' : str,
        schema.Optional('dir') : str,
        schema.Optional('if') : str,
        schema.Optional('rev') : str
    })

    # Checkout using CVS
    # - mandatory parameters: cvsroot, module
    # - optional parameters: rev, dir (dir is required if there are multiple checkouts)
    def __init__(self, spec):
        self.__recipe = spec['recipe']
        self.__cvsroot = spec["cvsroot"]
        self.__module = spec["module"]
        self.__rev = spec.get("rev")
        self.__dir = spec.get("dir", ".")

    def getProperties(self):
        return [{
            'recipe' : self.__recipe,
            'scm' : 'cvs',
            'cvsroot' : self.__cvsroot,
            'module' : self.__module,
            'rev' : self.__rev,
            'dir' : self.__dir
        }]

    def asScript(self):
        # If given a ":ssh:" cvsroot, translate that to CVS_RSH using ssh, and ":ext:"
        # (some versions of CVS do that internally)
        m = re.match('^:ssh:(.*)', self.__cvsroot)
        if m:
            prefix="CVS_RSH=ssh "
            rootarg=":ext:" + m.group(1)
        else:
            prefix=""
            rootarg=self.__cvsroot
        revarg = "-r {rev}".format(rev=self.__rev) if self.__rev != None else "-A"

        # Workaround: CVS 1.12.13 refuses to checkout with '-d .' when using remote access
        #   cvs checkout: existing repository /home/stefan/cvsroot does not match /home/stefan/cvsroot/cxxtest
        #   cvs checkout: ignoring module cxxtest
        # Thus, we have to trick it with a symlink.
        if re.match('^:ext:', rootarg) and self.__dir == '.':
            return """
# Checkout or update
if [ -d CVS ]; then
   {prefix}cvs -qz3 -d '{rootarg}' up -dP {revarg} .
else
   ln -s . __tmp$$
   {prefix}cvs -qz3 -d '{rootarg}' co {revarg} -d __tmp$$ '{module}'
   rm __tmp$$
fi
""".format(prefix=prefix, rootarg=rootarg, revarg=revarg, module=self.__module)
        else:
            return """
# Checkout or update
if [ -d {dir}/CVS ]; then
   {prefix}cvs -qz3 -d '{rootarg}' up -dP {revarg} {dir}
else
   {prefix}cvs -qz3 -d '{rootarg}' co {revarg} -d {dir} '{module}'
fi
""".format(prefix=prefix, rootarg=rootarg, revarg=revarg, module=self.__module, dir=self.__dir)

    def asDigestScript(self):
        # Describe what we do: just all the parameters concatenated.
        revarg = "-r {rev}".format(rev=self.__rev) if self.__rev != None else "-A"
        return "{cvsroot} {revarg} {module} {dir}".format(cvsroot=self.__cvsroot,
                                                          revarg=revarg,
                                                          module=self.__module,
                                                          dir=self.__dir)

    def merge(self, other):
        return False

    def getDirectories(self):
        return {self.__dir: hashString(self.asDigestScript())}

    def isDeterministic(self):
        # We cannot know whether this step is deterministic because we
        # don't know whether the given revision (if any) refers to a
        # tag or branch.
        return False

    def hasJenkinsPlugin(self):
        return False

    def status(self, workspacePath, dir, verbose = 0):
        print("CVS SCM status not implemented!")
        return 'error'

