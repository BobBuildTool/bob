Installation
************

Dependencies
============

Bob is built with Python3 (>=3.5) and needs the following additional packages
and Python modules that are not in the standard library to be build:

* `PyYAML`_. Either install via pip (``pip3 install PyYAML``) or the package
  that comes with your distribution (e.g. python3-yaml on Debian).
* `schema`_. Either install via pip (``pip3 install schema``) or the package
  that comes with your distribution (e.g. python3-schema on Debian).
* `python-magic`_. Either install via pip (``pip3 install python-magic``) or the
  package that comes with your distribution (e.g. python3-magic on Debian).
* `pyparsing`_. Either install via pip (``pip3 install pyparsing``) or the
  package that comes with your distribution (e.g. python3-pyparsing on Debian).
* gcc to build the namespace-sandbox feature
* `python3-sphinx`_. Only needed for generating man pages.

Apart from the build dependencies additional run time dependencies could arise,
e.g.:

* curl as the default URL SCM downloader
* extractors based on the supported extensions (e.g. 7z, xz, ...)
* source code management handlers (e.g. git, svn, ...)

.. _PyYAML: http://pyyaml.org/
.. _schema: https://pypi.org/project/schema/
.. _python-magic: https://pypi.org/project/python-magic/
.. _pyparsing: http://pyparsing.wikispaces.com/
.. _python3-sphinx: http://www.sphinx-doc.org/
.. _user namespaces: http://man7.org/linux/man-pages/man7/user_namespaces.7.html

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
should already be available (given that ``$(DESTDIR)/share/bash-completion/``
exists on your system). Otherwise simply source the script
contrib/bash-completion from your ~/.bashrc file. Optionally you can copy the
script to some global directory that is picked up automatically (e.g.  ``cp
contrib/bash-completion /etc/bash_completion.d/bob`` on Debian).

Zsh is able to understand the completion script too. Enable it with the
following steps::

   zsh$ autoload bashcompinit
   zsh$ bashcompinit
   zsh$ source contrib/bash-completion

Sanbox capabilities
===================

You might have to tweak your kernel settings in order to use the sandbox
feature. Bob uses Linux's `user namespaces`_ to run the build in a clean
environment. Check if ::

   $ cat /proc/sys/kernel/unprivileged_userns_clone
   1

yields "1". If the file exists and the setting is 0 you will get an "operation
not permitted" error when building. Add the line ::

   kernel.unprivileged_userns_clone = 1

to your ``/etc/sysctl.conf`` (or wherever your distro stores that).

