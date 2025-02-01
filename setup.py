# Bob build tool
# Copyright (C) 2019  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from distutils.command.build import build as build_orig
from setuptools import setup, find_packages, Command
from setuptools.dist import Distribution
import os
import sys


# Simple override of Distribution that forces "Root-Is-Purelib: false" on Linux
# because of the statically linked bob-namespace-sandbox applet. Use the
# original logic on other platforms.
class BinaryDistribution(Distribution):
    def has_ext_modules(self):
        return (sys.platform == "linux") or super().has_ext_modules()

# Additional command that builds the bob-namespace-sandbox applet on Linux.
class BuildApps(Command):
    description = "Build helper apps"
    user_options = []

    def enabled(self):
        return sys.platform == "linux"

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        os.makedirs("bin", exist_ok=True)
        self.spawn(["cc", "-o", "bin/bob-namespace-sandbox",
            "src/namespace-sandbox/namespace-sandbox.c",
            "src/namespace-sandbox/network-tools.c",
            "src/namespace-sandbox/process-tools.c",
            "-std=c99", "-Os", "-static", "-lm",
            "-ffunction-sections", "-fdata-sections", "-Wl,--gc-sections"])
        self.spawn(["strip", "bin/bob-namespace-sandbox"])

# Additional command to build manpages on POSIX systems
class BuildManpages(Command):
    description = "Build manpages"
    user_options = []

    def enabled(self):
        return sys.platform != "win32"

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        self.spawn(["sphinx-build", "-b", "man", "doc", "doc/_build/man"])

# Wrapper around build command to force automatic execution of the
# documentation and applet builds.
class build(build_orig):
    sub_commands = [
        ('build_apps', BuildApps.enabled ),
        ('build_man', BuildManpages.enabled ),
    ] + build_orig.sub_commands

cmdclass = {
    'build' : build,
    'build_apps': BuildApps,
    'build_man' : BuildManpages,
}
data_files = []

# Sphinx manpages and bash completion do not work on Windows
if sys.platform != 'win32':
    data_files.extend([
        ('share/man/man1', [
            'doc/_build/man/bob.1',
            'doc/_build/man/bob-archive.1',
            'doc/_build/man/bob-build.1',
            'doc/_build/man/bob-clean.1',
            'doc/_build/man/bob-dev.1',
            'doc/_build/man/bob-graph.1',
            'doc/_build/man/bob-jenkins.1',
            'doc/_build/man/bob-layers.1',
            'doc/_build/man/bob-ls.1',
            'doc/_build/man/bob-project.1',
            'doc/_build/man/bob-query-meta.1',
            'doc/_build/man/bob-query-path.1',
            'doc/_build/man/bob-query-recipe.1',
            'doc/_build/man/bob-query-scm.1',
            'doc/_build/man/bob-status.1',
        ]),
        ('share/man/man7', [
            'doc/_build/man/bobpaths.7',
        ]),
        ('share/bash-completion/completions', [
            'contrib/bash-completion/bob'
        ]),
    ])

# Sandbox helper is only built on Linux
if sys.platform == "linux":
    data_files.extend([
        ('bin', ['bin/bob-namespace-sandbox'])
    ])

setup(
    name = "BobBuildTool",
    use_scm_version = {
        # let setuptools_scm handle this
        'write_to' : "pym/bob/version.py",
        'local_scheme' : "no-local-version",
    },

    # Locate the python stuff. Exclude the development stuff in the release
    # version.
    packages = find_packages("pym", exclude=["bob.develop"]),
    package_dir = {'' : 'pym'},

    # The 'data_files' is used when acutally installing the package (either
    # directly, via bdist or bdist_wheel). In case of an sdist the MANIFEST.in
    # file makes sure that the sphinx input files are included.
    data_files = data_files,

    # Not quite a regular python package
    zip_safe=False,
    distclass=BinaryDistribution,
    cmdclass = cmdclass,

    # Our runtime dependencies
    python_requires = '>=3.8',
    install_requires = [
        'PyYAML',
        'schema',
        'python-magic',
        'pyparsing',
    ],

    # Optional dependencies that are not needed by default
    extras_require = {
        'azure' : [ 'azure-storage-blob' ],
    },

    # Installation time dependencies only needed by setup.py
    setup_requires = [
        'setuptools_scm',   # automatically get package version
    ],

    # Provide executables
    entry_points = {
        'console_scripts' : [
            'bob = bob.scripts:bob',
        ]
    },

    # Metadata for PyPI
    author = "Jan Klötzke",
    author_email = "jan@kloetzke.net",
    description = "Functional cross platform build-automation tool",
    long_description = "\n".join(open('README.md').read().splitlines()[2:]),
    long_description_content_type = 'text/markdown',
    license = "GPLv3+",
    keywords = "bob build-automation build-system",
    url = "https://bobbuildtool.github.io/",
    download_url = "https://github.com/BobBuildTool/bob/releases",
    classifiers = [
        'Development Status :: 5 - Production/Stable',
        'Environment :: Console',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)',
        'Operating System :: Microsoft :: Windows',
        'Operating System :: POSIX',
        'Programming Language :: Python :: 3 :: Only',
        'Topic :: Software Development :: Build Tools',
    ],
)
