.. _manpage-bob:

bob
===

.. only:: not man

   Name
   ----

   bob - Functional package build system

Synopsis
--------

::

    bob [-h] [-C DIRECTORY] [--version] [--debug DEBUG] [-i]
        [--color {never,always,auto}] [--query {nullset,nullglob,nullfail}]
        [command] ...


Description
-----------

Bob is a functional package build system.

Use ``bob help <command>`` to get the help for individual commands. The latest documentation
can be viewed at https://bob-build-tool.readthedocs.io.

Options
-------

``--color``
    Set color mode of console output. Defaults to ``auto`` which will use
    colors when the output is written to a terminal. Use ``never`` to always
    suppress color output or ``always`` to use colors even if the output is not
    written to a terminal.

``-C DIRECTORY, --directory DIRECTORY``
    Change to directory ``DIRECTORY`` before parsing recipes or user
    configuration. Multiple ``-C`` options stack, i.e. ``-C foo -C bar`` is
    equivalent to ``-C foo/bar``.

``--debug DEBUG``
    Debug options. These are subject to change and are left undocumented
    intentionally.

``-i``
    Ignore the :ref:`configuration-config-commands` section in all user
    configuration files. This will make sure that all commands use their
    default settings.

``--query``
    Set the behaviour of package queries when no package is matched. The path
    is evaluated from left to right and the policy is applied on the first
    occasion when an empty package set remained. See :ref:`bobpaths(7)
    <manpage-bobpaths>` for an further information about package queries.

    ``nullset``
        Empty sets of packages are considered a regular result and never
        treated as an error. This includes trivial path location steps where
        exact package names do not match.

    ``nullglob``
        Return an empty set of packages if the query involves wildcard name
        matches and/or predicates. Otherwise, that is if only direct name
        matches are used, an error is raised if a package name in the path does
        not match. This is the default.

    ``nullfail``
        An empty set of packages is always treated as an error.

``--version``
    Show version and exit.

User commands
-------------

The user commands are intended for interactive usage. Unless otherwise noticed,
their output is subject to improvements in future versions and usually not
intended to be parsed by scripts.

:ref:`manpage-archive`
    Manage binary artifact archives
:ref:`manpage-build`
    Build (sub-)packages in release mode
:ref:`manpage-clean`
    Delete unused src/build/dist paths
:ref:`manpage-dev`
    Build (sub-)packages in development mode
:ref:`manpage-graph`
    Make a interactive dependency graph
bob-help
    Display help information about command
:ref:`manpage-bob-init`
    Initialize build tree
:ref:`manpage-bob-jenkins`
    Configure Jenkins server
:ref:`manpage-layers`
    Update or show status of managed layers
:ref:`manpage-bob-ls`
    List package hierarchy. The output is suitable to be used in non-interactive scripts.
:ref:`manpage-bob-project`
    Create IDE project files
:ref:`manpage-show`
    Show properties of a package
:ref:`manpage-bob-status`
    Show SCM status

Script commands
---------------

The following commands are suitable to be used by non-interactive scripts.
Their output and behaviour is kept backwards compatible.

:ref:`manpage-query-meta`
    Query Package meta information
:ref:`manpage-bob-query-path`
    Query path information
:ref:`manpage-bob-query-recipe`
    Query package sources
:ref:`manpage-bob-query-scm`
    Query SCM information

