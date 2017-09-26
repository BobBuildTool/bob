Options
-------

``--always-checkout RE``
    Always checkout packages that match the regular expression pattern ``RE``.
    The option may be given more than once. In this case all patterns will be
    checked.

    Bob may skip the checkout of packages where a correct binary artifact can
    be downloaded from an archive. While this can dramatically decrease the
    build time of large projects it can hamper actually changing and rebuilding
    the packages with modifications. Use this option to instruct Bob to always
    checkout the sources of the packges that you may want to modify.

    This option will just make sure that the sources of matching packages are
    checked out. Bob will still try to find matching binary artifacts to skip
    the actual compilation of these packages. See the ``--download`` option
    to control what is built and what is downloaded.

``--clean``
    Do clean builds by clearing the build directory before executing the build
    commands. It will *not* clean all build results (e.g. like ``make clean``)
    but rather make sure that no old build artifacts are in the workspace when
    a package is rebuilt. To actually force a rebuild (even though nothing has
    changed) use ``-f``.

    This is the default for release mode builds. See ``--incremental`` for the
    inverse option.

``--clean-checkout``
    Do a clean checkout if SCM state is unclean.

``--destination DEST``
    Destination of build result (will be overwritten!)

``--download MODE``
    Download from binary archive (yes, no, deps, forced, forced-deps)

    no
      build given module and it's dependencies from sources
    yes
      download given module, if download fails - build it from sources
      (default for release mode)
    forced
      like 'yes' above, but fail if any download fails
    deps
      download dependencies of given module and build the module
      afterwards. If downloading of any dependency fails - build it
      from sources (default for develop mode)
    forced-deps
      like 'deps' above, but fail if any download fails
    forced-fallback
      combination of forced and forced-deps modes: if forced fails fall back to forced-deps

``--incremental``
    Reuse build directory for incremental builds

``--no-sandbox``
    Disable sandboxing

``--resume``
    Resume build where it was previously interrupted

``--sandbox``
    Enable sandboxing

``--upload``
    Upload to binary archive

``-B, --checkout-only``
    Don't build, just check out sources

``-D DEFINES``
    Override default environment variable

``-E``
    Preserve whole environment

``-b, --build-only``
    Don't checkout, just build and package

``-c CONFIGFILE``
    Use config File

``-e NAME``
    Preserve environment variable

``-f, --force``
    Force execution of all build steps

``-i, --installshared``
    Install shared packages to a given location (has to be combined with --shared)

``-s, --shared PATH``
    Shared packages will be searched at or installed to provided path

``-n, --no-deps``
    Don't build dependencies

``--no-logfiles``
    Don't write a logfile. Without this bob is creating a logfile in the
    current workspace. Because of the pipe-usage many tools like gcc,
    ls, git detect they are not running on a tty and disable output
    coloring. Disable the logfile generation to get the colored output
    back. 

``-p, --with-provided``
    Build provided dependencies too. In combination with ``--destination`` this
    is the default. In any other case ``--without-provided`` is default.

``-q, --quiet``
    Decrease verbosity (may be specified multiple times)

``-v, --verbose``
    Increase verbosity (may be specified multiple times)

``--without-provided``
    Build just the named packages without their provided dependencies. This is
    the default unless the ``--destination`` option is given too.


See also
--------

:ref:`bobpaths(7) <manpage-bobpaths>`
