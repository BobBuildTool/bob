bob-clean
=========

.. only:: not man

   Name
   ----

   bob-clean - Delete unused workspace and attic directories

Synopsis
--------

::

    bob clean [-h] [--develop | --release | --attic] [-c CONFIGFILE]
              [-D DEFINES] [--dry-run] [-f] [-s] [--sandbox | --no-sandbox]
              [-v]

Description
-----------

The *bob clean* command removes workspace directories from previous
:doc:`bob-dev` and :doc:`bob-build` invocations that are not referenced anymore
by the current recipes. It can also remove attic directories that are wasting
precious disk space.

The command has three modes of operation. By default develop mode related
workspaces are garbage collected. In release mode (``--release``) workspaces
that were created by *bob build* are cleaned. Lastly the ``--attic`` option
removes attic directories that were created by *bob dev*.

The identification of the unreferenced workspace directories is based on the
current recipes, user configuration files and environment definitions. You
should therefore pass the same options to *bob clean* (``-c``, ``-D``) that you
would also pass to *bob build* resp. *bob dev*. If in doubt use ``--dry-run``
to see what would be deleted.

Workspaces that hold source code are never deleted by default. Add the ``-s``
option to consider these workspace directories too. Bob will still check each
SCM in an unreferenced workspace for mdoifications.  If the SCM checkout has
been modified in any way (e.g. changed or untracked files, unpushed commits)
then the workspace is kept. Use ``-f`` to also delete such workspaces too.

Options
-------

``--develop``
    Clean develop mode (*bob dev*) directories. This is the default.

``--release``
    Clean release mode (*bob build*) directores.

``--attic``
    Remove attic directories.

.. include:: std-opt-cfg.rst

``--dry-run``
    Don't delete, just print what would be deleted.

``-f, --force``
    Remove source workspaces that have unsaved changes in their SCM(s).

    .. warning::
       Using this option *will* result in data loss if there are unsaved
       changes in checkout workspace directories. Use with great care.

``-s, --src``
    Clean source workspaces too. By default only build and package workspaces
    are considered.

    .. attention::
       You should double check with ``--dry-run`` that no unintended workspaces
       are actually deleted. While Bob can check SCMs that it knows it cannot
       detect all modifications, e.g. changes to extracted tar files.

``-v, --verbose``
    Print what is done.

