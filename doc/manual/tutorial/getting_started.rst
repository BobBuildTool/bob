Getting Started
***************

Installation
============

Bob needs the following additional Python modules that are not in the standard
library:

* python3-yaml

For the basic usage there is no installation needed. Just clone the repository
and compile::

   $ git clone git@github.com:BobBuildTool/bob.git
   $ cd bob
   $ make

and add this directory to your ``$PATH`` or set a symlink to ``bob`` from a
directory that is already in ``$PATH``.

You might have to tweak your kernel settings in order to use the sandbox
feature. Bob uses Linux's `user namespaces`_ to run the build in a clean
environment. Check if ::

   $ cat /proc/sys/kernel/unprivileged_userns_clone
   1

yields "1". If the file exists and the setting is 0 you will get an "operation
not permitted" error when building. Add the line ::

   kernel.unprivileged_userns_clone = 1

to your ``/etc/sysctl.conf`` (or wherever your distro stores that).

.. _user namespaces: http://man7.org/linux/man-pages/man7/user_namespaces.7.html

Getting shell completion
========================

Bob comes with a bash completion script. Simply source the script
contrib/bash-completion from your ~/.bashrc file. Optionally you can copy the
script to some global directory that is picked up automatically (e.g.
``cp contrib/bash-completion /etc/bash_completion.d/bob`` on Debian).

Zsh is able to understand the completion script too. Enable it with the
following steps::

   zsh$ autoload bashcompinit
   zsh$ bashcompinit
   zsh$ source contrib/bash-completion

