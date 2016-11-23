Options
-------

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
    Download from binary archive (yes, no, deps)

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

``-n, --no-deps``
    Don't build dependencies

``-q, --quiet``
    Decrease verbosity (may be specified multiple times)

``-v, --verbose``
    Increase verbosity (may be specified multiple times)

