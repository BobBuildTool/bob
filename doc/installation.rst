Installation
************

Dependencies
============

Bob is built with Python3 (>=3.5) and needs the following additional packages
and Python modules that are not part of the standard library:

* `PyYAML`_. Either install via pip (``python3 -m pip install PyYAML``) or the package
  that comes with your distribution (e.g. python3-yaml on Debian).
* `schema`_. Either install via pip (``python3 -m pip install schema``) or the package
  that comes with your distribution (e.g. python3-schema on Debian).
* `python-magic`_. Either install via pip (``python3 -m pip install python-magic``) or the
  package that comes with your distribution (e.g. python3-magic on Debian).
* `pyparsing`_. Either install via pip (``python3 -m pip install pyparsing``) or the
  package that comes with your distribution (e.g. python3-pyparsing on Debian).

To build bob you need the following tools:

* ``gcc``
* ``make``
* `python3-sphinx`_ (optional). Only needed for generating man pages.

Apart from the build dependencies additional run time dependencies could arise,
e.g.:

* GNU ``bash`` >= 4.x
* GNU coreutils (``cp``, ``ln``, ``sha1sum``, ...)
* GNU ``tar``
* ``hexdump``
* ``curl`` as the default URL SCM downloader
* source code management handlers as used (``curl``, ``cvs``, ``git``, ``svn``)
* extractors based on the supported extensions (``7z``, GNU ``tar``, ``gunzip``, ``unxz``, ``unzip``)
* ``azure-storage-blob`` Python library if the ``azure`` archive backend is
  used. Either install via pip (``python3 -m pip install azure-storage-blob``)
  or download from `GitHub <https://github.com/Azure/azure-storage-python>`_.

Build
=====

For the basic usage there is no installation needed. Just clone the repository
and compile::

   $ git clone https://github.com/BobBuildTool/bob.git
   $ cd bob
   $ make

and add this directory to your ``$PATH`` or set a symlink to ``bob`` from a
directory that is already in ``$PATH``. For regular usage it is recommended to
install Bob. The default install prefix is ``/usr/local`` which can be
overridden by defining ``DESTDIR``::

    $ make install DESTDIR=/usr

Shell completion
================

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
====================

You might have to tweak your kernel settings in order to use the sandbox
feature. Bob uses Linux's `user namespaces`_ to run the build in a clean
environment. Check if ::

   $ cat /proc/sys/kernel/unprivileged_userns_clone
   1

yields "1". If the file exists and the setting is 0 you will get an "operation
not permitted" error when building. Add the line ::

   kernel.unprivileged_userns_clone = 1

to your ``/etc/sysctl.conf`` (or wherever your distro stores that).


.. _PyYAML: http://pyyaml.org/
.. _schema: https://pypi.org/project/schema/
.. _python-magic: https://pypi.org/project/python-magic/
.. _pyparsing: http://pyparsing.wikispaces.com/
.. _python3-sphinx: http://www.sphinx-doc.org/
.. _user namespaces: http://man7.org/linux/man-pages/man7/user_namespaces.7.html
