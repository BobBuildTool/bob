# Bob build tool
# Copyright (C) 2019  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from pipes import quote

__all__ = ('mangleFingerprints')

snippets = [
    ("bob-libc-version", r"""
bob-libc-version()
{
    if ! type -p ${1:-${CC:-cc}} >/dev/null ; then
        echo "No C-Compiler!" >&2
        return 1
    fi

    # Machine type is important (e.g. x86_64)
    uname -m

    # Try glibc first
    cat >conftest.c <<EOF
#include <stdio.h>
#include <gnu/libc-version.h>
int main(){ printf("glibc %s\n", gnu_get_libc_version()); return 0; }
EOF
    if ${1:-${CC:-cc}} -o conftest conftest.c >/dev/null ; then
        ./conftest && return 0
    fi

    # Maybe musl libc? Link a simple program and extract runtime loader. On
    # musl the runtime loader is executable and outputs its version.
    cat >conftest.c <<EOF
int main(){ return 0; }
EOF
    if ! ${1:-${CC:-cc}} -o conftest conftest.c >/dev/null ; then
        echo "The C-Compiler does not seem to work... :(" >&2
        return 1
    fi

    DL=$(readelf -p .interp ./conftest | sed -n -e '/ld-musl/s/[^/]*\(\/.*\)/\1/p')
    if [[ -x $DL ]] ; then
        $DL 2>&1 || true
        return 0
    fi

    # Uhh?
    echo "Unsupported system. Please consider submitting your OS configuration for inclusion." >&2
    return 1
}
"""),

    ("bob-libstdc++-version", r"""
bob-libstdc++-version()
{
    if ! type -p ${1:-${CXX:-c++}} >/dev/null ; then
        echo "No C++-Compiler!" >&2
        return 1
    fi

    # Machine type is important (e.g. x86_64)
    uname -m

    cat >conftest.cpp <<EOF
#include <iostream>
int main(int /*argc*/, char ** /*argv*/)
{
    int ret = 1;
#ifdef __GLIBCXX__
    std::cout << "libstdc++ " << __GLIBCXX__ << &std::endl;
    ret = 0;
#endif
#ifdef _LIBCPP_VERSION
    std::cout << "libc++ " << _LIBCPP_VERSION << &std::endl;
    ret = 0;
#endif
    return ret;
}
EOF
    ${1:-${CXX:-c++}} -o conftest conftest.cpp >/dev/null
    ./conftest
}
""")
]

def mangleFingerprints(script, env):
    # do not add preamble for empty scripts
    if not script: return ""

    # Add snippets as they match and a default settings preamble
    ret = [script]
    for n,s in snippets:
        if n in script: ret.append(s)
    ret.extend(["set -o errexit", "set -o nounset", "set -o pipefail"])
    for n,v in sorted(env.items()):
        ret.append("export {}={}".format(n, quote(v)))
    ret.append('export BOB_CWD="$PWD"')
    return "\n".join(reversed(ret))

