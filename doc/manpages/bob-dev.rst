.. _manpage-dev:

bob-dev
=======

.. only:: not man

   Name
   ----

   bob-dev - Bob develop mode build

Synopsis
--------

::

    bob dev [-h] [--destination DEST] [-j [JOBS]] [-k] [-f] [-n] [-p]
            [--without-provided] [-A | --audit] [-b | -B | --normal]
            [--clean | --incremental] [--always-checkout RE] [--resume]
            [-q] [-v] [--no-logfiles] [-D DEFINES] [-c CONFIGFILE]
            [-e NAME] [-E] [-M META] [--upload] [--link-deps]
            [--no-link-deps] [--download MODE] [--download-layer MODE]
            [--shared | --no-shared] [--install | --no-install]
            [--sandbox | --no-sandbox] [--clean-checkout]
            [--attic | --no-attic]
            PACKAGE [PACKAGE ...]


Description
-----------

The *bob dev* command is building packages locally in develop mode. This mode
is intended to be used by developers to incrementally build the packages. Its
defaults are tuned to support active development on source code by keeping a
stable directory structure and minimize the edit-compile turnaround time.

Default options
---------------

By default *bob dev* works in the ``dev`` subdirectory of the project root
directory. The source-, build- and package-directories are kept in separate
hierarchies (``dev/src``, ``dev/build`` and ``dev/dist``) to allow easy
indexing of the involved sources by IDEs. It is possible to change these paths
by means of a plugin but it is advised to keep the top level structure.

In contrast to *bob build* the following options take precedence. They can be
overridden individually by their inverse switches:

* ``--download=deps``
* ``--incremental``
* ``--no-sandbox``

Source code checkout
--------------------

The source workspaces are updated incrementally as good as possible even across
recipe changes. This works quite well e.g. for git repositories. It could fail
silently on certain some recipes, though. On URL-SCMs the downloaded file can
be tracked by Bob. But if an archive is extracted Bob cannot reliably know
which files were coming from the archive. If the archive changes and files
vanish they will still be kept in the workspace.

If a binary archive is used Bob will try to skip the checkout of sources if
possible. This will work only if matching binary artifacts are available for
the current state of the recipes and their configuration. Though this will
typically speed up the build it can actually make working on the source code
difficult because not all involved sources are checked out.

There are a number of options to force the checkout of sources is such an
environment:

 * Use the ``--always-checkout`` option. If you're typically working on some
   particular packages then this option can force the checkout of these
   sources. It can be set in *default.yaml* so that it does not need to be
   specified every time again.
 * Make a dedicated build of selected packages. Because of ``--download=deps``
   the specified package will always be built from source.
 * Use ``--checkout-only`` to fetch the sources of a package and all its
   dependencies.

In any case Bob will use the sources once they were checked out. Bob will also
update them in subsequent builds.

.. include:: bob-build-dev.rst
