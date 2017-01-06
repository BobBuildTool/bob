Getting Started
***************

Installation
============

Bob is built with Python3 and needs the following additional packages and
Python modules that are not in the standard library:

* `PyYAML`_. Either install via pip (``pip3 install PyYAML``) or the package
  that comes with your distribution (e.g. python3-yaml on Debian).
* `schema`_. Either install via pip (``pip3 install schema``) or the package
  that comes with your distribution (e.g. python3-schema on Debian).
* `python-magic` Either install via pip (``pip3 install python-magic``) or the package
  that comes with your distribution.
* gcc
* `python3-sphinx` Only needed for generating man pages.

Python 3.3 or later should work. For the basic usage there is no installation
needed. Just clone the repository and compile::

   $ git clone https://github.com/BobBuildTool/bob.git
   $ cd bob
   $ make

and add this directory to your ``$PATH`` or set a symlink to ``bob`` from a
directory that is already in ``$PATH``. For regular usage it is recommended to
install Bob. The default install prefix is ``/usr/local`` which can be
overridden by defining ``DESTDIR``::

    $ make install DESTDIR=/usr

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
.. _schema: https://pypi.python.org/pypi/schema
.. _user namespaces: http://man7.org/linux/man-pages/man7/user_namespaces.7.html

Getting shell completion
========================

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

