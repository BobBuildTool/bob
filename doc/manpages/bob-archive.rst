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

    bob archive [-h] [-l | -a | -b name] subcommand ...

Available sub-commands:

::

    bob archive clean [-h] [--dry-run] [-n] [-v] [-f]
                      expression [expression ...]
    bob archive find [-h] [-n] [-v] [-f] expression [expression ...]
    bob archive scan [-h] [-v] [-f]

Description
-----------

The bob archive command can be used to manage binary artifact archives.
The command works on the archives defined in the user configuration :ref:`archive <configuration-config-archive>` .
The archives require the `managed` flag.
The archives to work on need to be explicitly specified (`-l`, `-a`, `-b`). These arguments are mutually exclusive.
Write access to the recipe root folder to create an index cache is required.

Artifacts are managed by the information included in their :ref:`Audit Trail
<audit-trail>`. See the Audit Trail documentation for a detailed description of
the included data. Currently the ``bob archive`` command has access to the
``meta``, ``build`` and ``metaEnv`` sections of the audit trail:

* ``build.date``: The date and time of the build (UTC, ISO 8601), e.g.
  "2019-12-02T13:19:34.193136+00:00".
* ``build.machine``: The hardware identifier as returned by the uname system call.
* ``build.nodename``: The host name.
* ``build.os-release``: Content of ``/etc/os-release``, if existing.
* ``build.release``: The operating system release.
* ``build.sysname``: The operating system name (e.g. "Linux").
* ``build.version``: The operating system version.
* ``meta.bob``: Bob version string.
* ``meta.jenkins-build-tag``: Jenkins ``$BUILD_TAG`` (only present on Jenkins builds).
* ``meta.jenkins-build-url``: Jenkins ``$BUILD_URL`` (only present on Jenkins builds).
* ``meta.jenkins-node``: Jenkins ``$NODE_NAME`` (only present on Jenkins builds).
* ``meta.language``: "bash" / "PowerShell"
* ``meta.package``: Package path of the artifact that was built.
* ``meta.recipe``: Name of the recipe that declared the package.
* ``meta.step``: "src" / "build" / "dist"
* ``metaEnv.<VAR>``: Value of :ref:`metaEnvironment <configuration-recipes-metaenv>`
  variable ``<VAR>``.

.. attention::
   Be careful when matching by ``meta.package``. The retention expression (see
   ``clean`` command below) has to match an actually present artifact. There
   may be more than one possible path trough the dependency tree to the same
   package.  It is also possible that multiple packages produce the identical
   result. Only one such package will usually be built by Bob. None of these
   alternate possible package paths are recorded so you should double check if
   you query actually maches.

Options
-------

``-l, --local``
    Instead of working with the archives defined in the user configuration, the command will operate in the working directory.
    This can be useful for automated tasks running on a server. Make sure to have write access to the working directory.
``-a, --all``
    Execute the command on all suitable archives defined in the user configuration.
``-b, --backend NAME``
    `NAME` is the name of the archive defined in the user configuration. Multiple archives may be provided by repeatedly using `-b`.
    The command is executed on all of those archives.
``--dry-run``
    Do not actually delete any artifacts but show what would get removed.

``-n``
    Don't rescan the archive for new artifacts. The command will work on the
    last scanned data. Useful if the scan takes a long time (e.g. big archive
    on network mount) and was already run recently.

``-v``
    Be a bit more chatty on what is done.

``-f``
    Return a non-zero exit code in case of errors

Commands
--------

clean
    Remove unneeded artifacts from the archive.

    The command takes one or more retention expressions. Any artifact that is
    matched by at least one of the expressions or referenced transitively by a
    matched artifact is kept. If an artifact is neither matched by any
    expression nor referenced by a retained artifact it is deleted.

    The expression language has the following general syntax:

         *Predicate* [``LIMIT`` *Limit* [``ORDER BY`` *Field* [``ASC`` | ``DESC``]]]

    The *Predicate* supports the following constructs:

    * Strings are written with double quotes, e.g. ``"foo"``. To embed
      double quotes in the string itself escape them with ``\``.
    * Certain fields from the audit trail can be accessed by their name.
      Sub-fields are specified with a dot operator, e.g. ``meta.package``. All
      fields are case sensitive and of string type. Referencing a non-existing
      field is supported but will yield a distinct "undefined" value. This
      special value can only be compared with ``==`` and ``!=`` with other
      values.
    * Strings and fields can be compared by the following operators (in
      decreasing precedence): ``<``, ``<=``, ``>``, ``>=``, ``==``, ``!=``.
      They are compared character by character by their unicode code point. If
      the end of a operand is reached before finding a difference the string
      lengths are compared instead.
    * String comparisons can be logically combined with ``&&`` (and)
      respectively ``||`` (or). There is also a ``!`` (not) logical operator.
    * Parenthesis can be used to override precedence.

    The optional *Limit* field must be an integer number greater than zero. It
    limits the number of artifacts that are retained by *Predicate*. If no
    *Limit* is specified all matching artifacts are retained. By default the
    artifacts are sorted by the ``build.date`` field in descending order so
    that only the most recent *Limit* artifacts are retained.  If *Field* is
    not populated the artifact is always put at the end of the list. Specify
    ``ASC`` to sort the artifacts in ascending order by *Field*.

    A typical usage of the ``clean`` command is to remove old artifacts from a
    continuous build artifact archive. Suppose the root package that is built
    is called ``platform/app`` and we want to retain only artifacts that are
    referenced by builds that are at most seven days old::

        bob archive clean "meta.package == \"platform/app\" && \
                           build.date >= \"$(date -u -Idate -d-7days)\""

    The following example retains only the last three builds from a recipe::

        bob archive clean 'meta.recipe == "root" LIMIT 3'

    Both examples above can be combined, e.g. to keep all builds of the last
    week while making sure that at least the last build is kept, even if that
    build is older. ::

        bob archive clean "meta.package == \"platform/app\" && \
                           build.date >= \"$(date -u -Idate -d-7days)\"" \
                          'meta.package == \"platform/app\" LIMIT 1'

find
    Find artifacts matching a retention expression.

    This expressions that can be given to this command are the same as for the
    ``clean`` command above. All artifacts that match at least one of the
    expressions are printed on stdout. Use this command to search for
    particular artifacts or to check that you retention expressions actually
    match the intended artifacts.


scan
    Scan for added artifacts.

    The ``archive`` command keeps a cache of all indexed artifacts. To freshen
    this cache use this command. Even though other sub-commands will do a scan
    too (unless suppressed by ``-n``) it might be helpful to do the scan on a
    more convenient time. If the archive is located e.g. on a slow network
    drive it could be advantageous to scan the archive with a cron job over
    night.

Notes
-----

``bob archive`` only works for local binary artifact archives. If you're using a
remote archive, you need shell access and a working Bob installation on the
machine providing your archive in order to be able to use ``bob archive``.
