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

    Bob will check all SCMs for local changes at the start of a checkout. If a
    SCM checkout is tainted (e.g. dirty, switchted branch, unpushed commits,
    ...) Bob will move it into the attic and do a fresh checkout.

    Use this option if you are not sure about the state of the source code. You
    can also use ':ref:`bob status <manpage-bob-status>`' to check the state
    without changing it.

``--destination DEST``
    Destination of build result (will be overwritten!)

    All build results are copied recursively into the given folder. Colliding
    files will be overwritten but other existing files or directories are kept.
    Unless ``--without-provided`` is given using this option will implicitly
    enable ``--with-provided`` to build and copy all provided packages of the
    built package(s).

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
      combination of forced and forced-deps modes: if forced fails fall back to
      forced-deps

``--incremental``
    Reuse build directory for incremental builds.

    This is the inverse option to ``--clean``. Build workspaces will be reused
    as long as their recipes were not changed. If the recipe did change Bob
    will still do a clean build automatically.

``--link-deps``
    Create symlinks to dependencies next to workspace.

``--no-sandbox``
    Disable sandboxing

``--resume``
    Resume build where it was previously interrupted.

    All packges that were built in the previous invocation of Bob are not
    checked again. In particular changes to the source code of these packages
    are not considered. Use this option to quickly resume the build if it
    failed and the error has been corrected in the failing packge.

``--sandbox``
    Enable sandboxing

``--upload``
    Upload to binary archive

``-B, --checkout-only``
    Don't build, just check out sources

``-D DEFINES``
    Override default environment variable

``-E``
    Preserve whole environment.

    Normally only variables configured in the whitelist are passed unchanged
    from the environment. With this option all environment variables that are
    set while invoking Bob are kept. Use with care as this might affect some
    packages whose recipes are not robust.

``-b, --build-only``
    Don't checkout, just build and package

    If the sources of a package that needs to be built are missing then Bob
    will still check them out. This option just prevents updates of existing
    source workspaces.

``-c CONFIGFILE``
    Use additional configuration file.

    The ``.yaml`` suffix is appended automatically and the configuration file
    is searched relative to the project root directory unless an absolute path
    is given. Bob will parse these user configuration files after
    *default.yaml*. They are using the same schema.

    This option can be given multiple times. The files will be parsed in the
    order as they appeared on the command line.

``-e NAME``
    Preserve environment variable.

    Unless ``-E`` this allows the fine grained addition of single environment
    variables to the whitelist.

``-f, --force``
    Force execution of all build steps.

    Usually Bob decides if a build step or any of its input has changed and
    will skip the execution of it if this is not the case. With this option Bob
    not use that optimization and will execute all build steps.

``-j, --jobs``
    Specifies the number of jobs to run simultaneously.

    Any checkout/build/package step that needs to be executed are counted as a
    job. Downloads and uploads of binary artifacts are separate jobs too. If a
    job fails the other currently running jobs are still finished before Bob
    returns. No new jobs are scheduled, though, unless the ``-k`` option is
    given (see below).

    If the -j option is given without an argument, Bob will run as many jobs as
    there are processors on the machine.

``-k, --keep-going``
    Continue  as much as possible after an error.

    While the package that failed to build and all the packages that depend on
    it cannot be built either, the other dependencies are still processed.
    Normally Bob stops on the first error that is encountered.

``-n, --no-deps``
    Don't build dependencies.

    Only builds the package that was given on the command line. Bob will not
    check if the dependencies of that package are available and if they are
    up-to-date.

``--no-link-deps``
    Do not create symlinks to dependencies next to workspace.

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

:ref:`bobpaths(7) <manpage-bobpaths>` :ref:`bob-status(1) <manpage-bob-status>`
