.. _manpage-build:

bob-build
=========

.. only:: not man

   Name
   ----

   bob-build - Bob release mode build

Synopsis
--------

::

    bob build [-h, --help] [--destination DEST] [-j, --jobs [JOBS]] [-k, --keep-going] [-f, --force]
              [-n, --no-deps] [-p, --with-provided] [--without-provided] [-A | --no-audit]
              [-b, --build-only | -B, --checkout-only | --normal]
              [--clean | --incremental] [--always-checkout RE] [--resume]
              [-q, --quiet] [-v, --verbose] [--no-logfiles] [-D VAR=VALUE] [-c CONFIGFILE]
              [-lc LAYERCONFIG] [-e NAME] [-E] [-M VAR=VALUE] [--upload]
              [--link-deps] [--no-link-deps] [--download MODE]
              [--download-layer MODE] [--shared | --no-shared]
              [--install | --no-install]
              [--sandbox | --slim-sandbox | --dev-sandbox | --strict-sandbox | --no-sandbox]
              [--clean-checkout] [--attic | --no-attic]
              PACKAGE [PACKAGE ...]

Description
-----------

The *bob build* command is building packages locally in release mode. This mode
is intended to provide maximum correctness at the expense of build time and
disk requirements.

Default options
---------------

By default *bob build* works in the ``work`` subdirectory of the project root
directory. The source-, build- and package-directories of packages are kept
next to each other (``work/<pkg>/src``, ``work/<pkg>/build`` and
``work/<pkg>/dist``). The ``<pkg>``-subdirectories are derived from the package
name. As recipes are changed Bob will always use a new, dedicated directory for
each variant by adding a counting suffix to the above directories.

In contrast to *bob dev* the following options take precedence. They can be
overridden individually by their inverse switches:

* ``--download=yes``
* ``--clean``
* ``--sandbox``

.. include:: bob-build-dev.rst
