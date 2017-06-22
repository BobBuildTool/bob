.. _manpage-archive:

bob-archive
===========

.. only:: not man

   Name
   ----

   bob-archive - Manage binary artifacts archive

Synopsis
--------

Generic command format:

::

    bob archive [-h] subcommand ...

Available sub-commands:

::

    bob archive clean [-h] [--dry-run] [-n] [-v] expression
    bob archive scan [-h] [-v]

Description
-----------

The bob archive command can be used to manage binary artifact archives. The
command must be executed in the root of the archive and needs write access to
create an index cache.

Artifacts are managed by the information included in their
:ref:`Audit Trail <audit-trail>`. See the Audit Trail documentation about the
general included data. Currently the ``bob archive`` command has access to the
``meta``, ``build`` and ``metaEnv`` sections of the audit trail.

Options
-------

``--dry-run``
    Do not actually delete any artifacts but show what would get removed.

``-n``
    Don't rescan the archive for new artifacts. The command will work on the
    last scanned data. Useful if the scan takes a long time (e.g. big archive
    on network mount) and was already run recently.

``-v``
    Be a bit more chatty on what is done.

Commands
--------

clean
    Remove unneeded artifacts from the archive.

    The command takes a single argument as the retention expression. Any
    artifact that is matched by the expression or referenced by such other
    artifact is kept. If an artifact is neither matched by the given expression
    nor referenced by a retained artifact it is deleted.

    The expression language supports the following constructs:

    * Strings are written with double quotes, e.g. ``"foo"``. To embed
      double quotes in the string itself escape them with ``\``.
    * Certain fields from the audit trail can be accessed by their name.
      Sub-fields are specified with a dot operator, e.g. ``meta.package``. All
      fields are case sensitive and of string type.
    * Strings and fields can be compared by the following operators (in
      decreasing precedence): ``<``, ``<=``, ``>``, ``>=``, ``==``, ``!=``.
      They have the same semantics as in Python.
    * String comparisons can be logically combined with ``&&`` (and)
      respectively ``||`` (or). There is also a ``!`` (not) logical operator.
    * Parenthesis can be used to override precedence.

    A typical usage of the ``clean`` command is to remove old artifacts from a
    continuous build artifact archive. Suppose the root package that is built
    is called ``platform/app`` and we want to retain only artifacts that are
    referenced by builds that are at most seven days old::

        bob archive clean "meta.package == \"platform/app\" && \
                           build.date >= \"$(date -u -Idate -d-7days)\""

scan
    Scan for added artifacts.

    The ``archive`` command keeps a cache of all indexed artifacts. To freshen
    this cache use this command. Even though other sub-commands will do a scan
    too (unless suppressed by ``-n``) it might be helpful to do the scan on a
    more convenient time. If the archive is located e.g. on a slow network
    drive it could be advantageous to scan the archive with a cron job over
    night.

