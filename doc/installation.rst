Installation
************

Dependencies
============

Bob is built with Python3 (>=3.8). Some additional Python packages are
required. They are installed automatically as dependencies.

Apart from the Python dependencies additional run time dependencies could arise,
e.g.:

* GNU ``bash`` >= 4.x
* Microsoft PowerShell
* GNU coreutils (``cp``, ``ln``, ``sha1sum``, ...)
* GNU ``tar``
* ``hexdump``
* ``curl`` as the default URL SCM downloader
* source code management handlers as used (``curl``, ``cvs``, ``git`` >= 2.13.0, ``svn``)
* extractors based on the supported extensions (``7z``, GNU ``tar``, ``gunzip``, ``unxz``, ``unzip``)
* ``azure-storage-blob`` Python library if the ``azure`` archive backend is
  used. Either install via pip (``python3 -m pip install azure-storage-blob``)
  or download from `GitHub <https://github.com/Azure/azure-storage-python>`_.

The actually needed dependencies depend on the used features and the operating
system.

.. _installation-install:

Install
=======

There are several options how to install Bob on your system. If in doubt stick
to the standard ``pip`` method.

If you are unfamiliar with the installation of Python packages make sure to
read `Installing Packages <https://packaging.python.org/tutorials/installing-packages/>`_
from the Python Packaging User Guide. The instructions below assume that you
have installed Python and that it is available on the command line.

Supported Platforms
-------------------

* Linux
* Windows 10
* `MSYS2`_ (Windows 10)
* Other POSIX platforms should work but are not actively tested

See below for platform specific installation notes.

PyPI release versions
---------------------

To get the latest released version just use ``pip`` to download the package and
its depedencies from PyPI::

   $ python3 -m pip install BobBuildTool [--user]

Release versions are supposed to be stable and keep backwards compatibility.

Install latest development version
----------------------------------

If you want to test pre-release versions you can instruct ``pip`` to fetch
and build the package directly from git::

   $ python3 -m pip install --user git+https://github.com/BobBuildTool/bob

Note that during development minor breakages can occur.

Hacking on Bob
--------------

For the basic hacking there is no installation needed. Just clone the
repository::

   $ git clone https://github.com/BobBuildTool/bob.git
   $ cd bob

and add this directory to your ``$PATH`` or set a symlink to ``bob`` from a
directory that is already in ``$PATH``. You will have to manually install all
required dependencies and the bash completion, though.

.. attention::
   The ``pip install -e .`` resp. ``python3 setup.py develop`` commands do
   *not* work for Bob. The problem is that these installation variants are only
   really working for pure python projects. In contrast to that Bob comes with
   manpages and C helper applets that are not built by these commands.

The following additional packages and Python modules that are not part of the
standard library and need to be installed:

* `PyYAML`_. Either install via pip (``python3 -m pip install PyYAML``) or the package
  that comes with your distribution (e.g. python3-yaml on Debian).
* `schema`_. Either install via pip (``python3 -m pip install schema``) or the package
  that comes with your distribution (e.g. python3-schema on Debian).
* `python-magic`_. Either install via pip (``python3 -m pip install python-magic``) or the
  package that comes with your distribution (e.g. python3-magic on Debian).
* `pyparsing`_. Either install via pip (``python3 -m pip install pyparsing``) or the
  package that comes with your distribution (e.g. python3-pyparsing on Debian).

To fully run Bob you need the following tools:

* ``gcc``
* `python3-sphinx`_

The compiler is only required on Linux.

Offline installation
--------------------

In case you need to install Bob on machines without internet access the following commands
may give you some hints how to do this:

On a machine with internet access download the required packages. ::

   $ mkdir -p bob_install && cd bob_install
   $ pip3 download BobBuildTool -d .
   $ pip3 download sphinx -d .

After this transfer the bob_install folder to your offline machine and
install bob, but install the dependencies first. Otherwise they are not
found or maybe in a wrong version already installed. ::

   $ pip3 install --no-index --find-links /path/to/bob_install Setuptools
   $ pip3 install --no-index --find-links /path/to/bob_install Sphinx
   $ pip3 install --no-index --find-links /path/to/bob_install BobBuildTool

Maybe there are some other dependencies missing, e.g. setuptools,
setuptools_scm, wheel,...

Linux/POSIX platform notes
==========================

.. _installation-recommended-config:

Recommended configuration
-------------------------

It is recommended to create a user global Bob configuration file which applies
to all projects. The following settings will ensure that shareable packages are
put into a common location and that downloaded source tarballs are mirrored
locally::

    preMirrorPrepend:
        scm: url
        url: "https?://.*/(.*)"
        mirror: "~/.cache/bob/mirror/\\1"
        upload: True
    share:
        path: ~/.cache/bob/pkgs
        quota: "5G"
        autoClean: True

The above configuration should be stored as ``~/.config/bob/default.yaml``. See
:ref:`configuration-config-mirrors` and :ref:`configuration-config-share` for
more details.

Shell completion
----------------

Bob comes with a bash completion script. If you installed Bob the completion
should already be available (given that ``$(DESTDIR)/share/bash-completion/completions``
exists on your system). Otherwise simply source the script
contrib/bash-completion/bob from your ~/.bashrc file. Optionally you can copy the
script to some global directory that is picked up automatically (e.g.  ``cp
contrib/bash-completion/bob /etc/bash_completion.d/bob`` on Debian).

Zsh is able to understand the completion script too. Enable it with the
following steps::

   zsh$ autoload bashcompinit
   zsh$ bashcompinit
   zsh$ source contrib/bash-completion/bob

Sandbox capabilities
--------------------

You might have to tweak your kernel settings in order to use the sandbox
feature. Bob uses Linux's `user namespaces`_ to run the build in a clean
environment. Check if ::

   $ cat /proc/sys/kernel/unprivileged_userns_clone
   1

yields "1". If the file exists and the setting is 0 you will get an "operation
not permitted" error when building. Add the line ::

   kernel.unprivileged_userns_clone = 1

to your ``/etc/sysctl.conf`` (or wherever your distro stores that).

.. _installation-windows:

Windows platform notes
======================

Bob can be used in two flavours on Windows: as native application or in a
`MSYS2`_ POSIX environment. Unless your recipes need Unix tools the native
installation is recommended.

Native usage
------------

Python comes with
`extensive documentation <https://docs.python.org/3/using/windows.html>`_
about how to install it on Windows. Only the full installer has been tested but
the other methods should probably work as well.

Make sure to add the Python interpreter to ``%PATH%``. If your recipes use Bash
you must additionally install `MSYS2`_ and add the path to ``bash.exe`` *after*
the native Python interpreter. Otherwise the MSYS2 Python interpreter might be
invoked which does not work.

.. note::

   Windows path lengths have historically been limited to 260 characters.
   Starting with Windows 10 the administrator can activate the "Enable Win32
   long paths" group policy or you may set the
   ``HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\FileSystem@LongPathsEnabled``
   registry key to ``1``. Either option is sufficient to remove the path length
   limitation.

If you want to install Bob for all users, make sure you have installed Python
for all users and that you run ``pip`` with administrative rights. Otherwise
the installation will only be done for the current user!

MSYS2
-----

Follow the standard MSYS2 installation. Then install ``python3`` and
``python-pip`` and use one of the install methods above.

.. _PyYAML: http://pyyaml.org/
.. _schema: https://pypi.org/project/schema/
.. _python-magic: https://pypi.org/project/python-magic/
.. _pyparsing: http://pyparsing.wikispaces.com/
.. _python3-sphinx: http://www.sphinx-doc.org/
.. _user namespaces: http://man7.org/linux/man-pages/man7/user_namespaces.7.html
.. _MSYS2: https://www.msys2.org/
