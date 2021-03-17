.. _manpage-clean:

bob-clean
=========

.. only:: not man

   Name
   ----

   bob-clean - Delete unused workspace, attic and shared directories

Synopsis
--------

::

    bob clean [-h] [--develop | --release | --attic | --shared]
              [-c CONFIGFILE] [-D DEFINES] [--dry-run] [-f] [-s]
              [--all-unused] [--used] [--sandbox | --no-sandbox] [-v]


Description
-----------

The *bob clean* command removes workspace directories from previous
:doc:`bob-dev` and :doc:`bob-build` invocations that are not referenced anymore
by the current recipes. It can also remove attic directories that are wasting
precious disk space.

The command has several modes of operation. By default develop mode related
workspaces are garbage collected. In release mode (``--release``) workspaces
that were created by *bob build* are cleaned. Giving the ``--attic`` option
removes attic directories that were created by *bob dev*. Lastly the
``--shared`` option will cleanup the optionally configured :ref:`shared package
location <configuration-config-share>`.

The identification of the unreferenced workspace directories is based on the
current recipes, user configuration files and environment definitions. You
should therefore pass the same options to *bob clean* (``-c``, ``-D``) that you
would also pass to *bob build* resp. *bob dev*. If in doubt use ``--dry-run``
to see what would be deleted.

Workspaces that hold source code are never deleted by default. Add the ``-s``
option to consider these workspace directories too. Bob will still check each
SCM in an unreferenced workspace for modifications.  If the SCM checkout has
been modified in any way (e.g. changed or untracked files, unpushed commits)
then the workspace is kept. Use ``-f`` to also delete such workspaces too.

In contrast to the other modes the ``--shared`` option does not work on the
project directory itself but the shared packages location. If no quota was
configured the command will do nothing by default. Otherwise all *unused*
packages will be removed (from oldest usage to newest) until the quota is met.
If no unused packages are remaining (i.e. all other packages are still
referenced by a project workspace) the quota could still be exceeded. To remove
older packages too in such a case, add the ``--used`` option. To remove *all*
unused packages, regardless of the quota, use the ``--all-unused`` option.

Options
-------

``--develop``
    Clean develop mode (*bob dev*) directories. This is the default.

``--release``
    Clean release mode (*bob build*) directories.

``--attic``
    Remove attic directories.

``--shared``
    Delete packages from shared location.

.. include:: std-opt-cfg.rst

``--all-unused``
    Normally unused packages from a shared location are only deleted if the
    quota is exceeded. Use this option to delete all unused packages to free
    even more disk space.

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

``--used``
    If a package is still used by a project on the machine it is not deleted by
    default even if the quota is still exceeded. By giving this option such
    packages are considered too. Note that this will most likely lead to
    rebuilds/downloads of packages in the affected projects.

``-v, --verbose``
    Print what is done.

