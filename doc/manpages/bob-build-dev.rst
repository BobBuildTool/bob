Build tree location
-------------------

The build can be done directly in the project root directory or in a separate
directory. To build outside of the project directory the build-tree must first
be initialized with :ref:`bob init <manpage-bob-init>`. Any number of build
trees may refer to the same project. Inside the external build-tree there may
be a dedicated ``default.yaml``, overriding settings from the project.

Sandboxing
----------

Sandboxing allows to execute the build steps in ephemeral containers. The
feature is currently available on Linux only. There are different aspects to
sandboxing:

1. Isolating from the host environment. By using a project defined sandbox
   image, the build environment is made independent of the host Linux
   distribution.
2. Controlling the accessible project paths. Only declared dependencies are
   accessible read-only. The build workspace is the only writable path (despite
   ``/tmp``). All other project paths are not not accessible at all.
3. Providing stable execution paths. Sometimes the build path is leaking into
   the created binaries. Inside the sandbox environment, the paths can be made
   reproducible.

.. only:: not man

    To accommodate for different use cases, five different sandbox modes are
    supported by Bob. They differ in their degree of isolation and execution path
    stability:

    +----------------------+--------------------------------+------------------------------------------+
    | Mode                 | Packages without sandbox image | Packages with sandbox image              |
    |                      +-------------+------------------+-----------+-------------+----------------+
    |                      | Isolation   | Execution path   | Isolation | Image used? | Execution path |
    +======================+=============+==================+===========+=============+================+
    | ``--no-sandbox``     | \-          |  Workspace       |  \-       | n/a         | Workspace      |
    +----------------------+-------------+------------------+-----------+-------------+----------------+
    | ``--sandbox``        | \-          |  Workspace       |  Yes      | Yes         | Stable         |
    +----------------------+-------------+------------------+-----------+-------------+----------------+
    | ``--slim-sandbox``   | Yes         |  Workspace       |  Yes      | \-          | Workspace      |
    +----------------------+-------------+------------------+-----------+-------------+----------------+
    | ``--dev-sandbox``    | Yes         |  Workspace       |  Yes      | Yes         | Workspace      |
    +----------------------+-------------+------------------+-----------+-------------+----------------+
    | ``--strict-sandbox`` | Yes         |  Stable          |  Yes      | Yes         | Stable         |
    +----------------------+-------------+------------------+-----------+-------------+----------------+

    The overall behaviour depends on the availability of a sandbox image. Such
    an image must be provided by a recipe via
    :ref:`configuration-recipes-provideSandbox` and the sandbox image must have
    been picked up by a ``use: [sandbox]`` dependency.

    The execution path is the path where the checkout/build/packageScript is
    executed. This is usually the *workspace* path but some modes use a
    *stable* path instead. Stable paths start with ``/bob/...`` and are computed
    from the :term:`Variant-Id` of the step. An unchanged step will always be
    executed at the same stable path in a sandbox.

Using ``--no-sandbox`` will not use any sandboxing features and all build steps
are executed without any isolation on the build host. The ``--sandbox`` option
will provide partial isolation only if a sandbox image is available for a package.
Inside the sandbox image all paths are stable, i.e. independent of the
workspace path. As a light-weight alternative, the ``--slim-sandbox`` option
will always provide isolation but an available sandbox image is not used and
all workspace paths are retained. Likewise, the ``--dev-sandbox`` option will
also provide full isolation but an available sandbox image is used. The
``--strict-sandbox`` option further uses stable paths consistently.


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
    checkout the sources of the packages that you may want to modify.

    This option will just make sure that the sources of matching packages are
    checked out. Bob will still try to find matching binary artifacts to skip
    the actual compilation of these packages. See the ``--download`` and
    ``--download-layer`` option to control what is built and what is downloaded.

``--attic``
    Move checkout workspace to attic if inline SCM switching is not possible.
    (Default)

``--audit``
    Generate an audit trail when building.

    This is the default unless the user changed it in ``default.yaml``.

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
    SCM checkout is tainted (e.g. dirty, switched branch, unpushed commits,
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

``--dev-sandbox``
    Enable development sandboxing.

    Always build packages in an isolated environment where only declared
    dependencies are visible. If a sandbox image is available, it is used.
    Otherwise the host paths are made read-only.

``--download MODE``
    Download from binary archive (yes, no, deps, forced, forced-deps, packages)

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
    packages=<packages regex>
      download modules that match a given regular expression, build all other.

``--download-layer MODE``
    Download from binary archive for layer (yes, no, forced)

    Acts like ``--download`` but only for the modules of the layer that match a
    given regular expression (``--download`` option will be overwritten for
    matching modules).
    Can be used multiple times (if regex is used also multiple times the last mode wins).

    no=<layer regex>
      build modules of a layer that match a given regular expression from sources
    yes=<layer regex>
      download modules of a layer that match a given regular expression, if download fails - build it from sources
    forced=<layer regex>
      like 'yes' above, but fail if any download fails

``--incremental``
    Reuse build directory for incremental builds.

    This is the inverse option to ``--clean``. Build workspaces will be reused
    as long as their recipes were not changed. If the recipe did change Bob
    will still do a clean build automatically.

``--install``
    Install shared packages. A shared location must have been configured so
    that Bob knows where to put the package. This is the default.

``--link-deps``
    Create symlinks to dependencies next to workspace.

``--no-install``
    Do not install shared packages if a shared location is configured.

``--no-sandbox``
    Disable sandboxing

``--no-shared``
    Do not use shared packages even if they are available.

``--resume``
    Resume build where it was previously interrupted.

    All packages that were built in the previous invocation of Bob are not
    checked again. In particular changes to the source code of these packages
    are not considered. Use this option to quickly resume the build if it
    failed and the error has been corrected in the failing package.

``--sandbox``
    Enable partial sandboxing.

    Build packages in an ephemeral container if a sandbox image is available
    for the package. Inside the sandbox, stable execution paths are used. In
    absence of a sandbox image, no isolation is performed.

``--shared``
    Use shared packages if they are available. This is the default.

``--slim-sandbox``
    Enable slim sandboxing.

    Build packages in an isolated mount namespace. Most of the host paths
    are available read-only. Other workspaces are hidden when building a
    package unless they are a declared dependency. An optionally available
    sandbox image is *not* used.

``--strict-sandbox``
    Enable strict sandboxing.

    Always build packages in an isolated environment where only declared
    dependencies are visible. If a sandbox image is available, it is used.
    Otherwise the host paths are made read-only. The build path is always
    a reproducible, stable path.

``--upload``
    Upload to binary archive

``-A, --no-audit``
    Do not generate an audit trail.

    The generation of the audit trail is usually barely noticeable. But if a
    large number of repositories is checked out it can add a significant
    overhead nonetheless. This option suppresses the generation of the audit
    trail.

    Note that it is not possible to upload such built artifacts to a binary
    archive because vital information is missing. It is also not possible to
    install shared packages that were built without audit trail for the same
    reason.

``-B, --checkout-only``
    Don't build, just check out sources

``-D VAR=VALUE``
    Override default or set environment variable.

    Sets the variable ``VAR`` to ``VALUE``. This overrides the value possibly
    set by ``default.yaml``, config files passed by ``-c`` or any file that was
    included by either of these files.

``-E``
    Preserve whole environment.

    Normally only variables configured in the whitelist are passed unchanged
    from the environment. With this option all environment variables that are
    set while invoking Bob are kept. Use with care as this might affect some
    packages whose recipes are not robust.

``-M VAR=VALUE``
   Assign the meta variable ``VAR`` to the given value in the audit trail.
   The variable can later be matched by :ref:`bob archive <manpage-archive>` as
   ``meta.VAR`` to select artifacts built by this project. Variables that are
   defined by Bob itself (e.g. ``meta.bob``) cannot be redifined!

``-b, --build-only``
    Don't checkout, just build and package. Checkout scripts whose
    :ref:`configuration-recipes-checkoutUpdateIf` property was evaluated as
    true will still be run.

    If the sources of a package that needs to be built are missing then Bob
    will still check them out. This option just prevents updates of existing
    source workspaces that are fetched from remote locations. A notable
    exception is the ``import`` SCM which will still update the workspace even
    if this option is present.

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

``-lc LAYERCONFIG``
    Use additional layer configuration file.

    This is special kind of configuration file to control the layers checkout. Only
    ``layersWhitelist`` and ``layersScmOverrides`` are supported. Layers are
    updated automatically unless ``--build-only`` is given too.

    The ``.yaml`` suffix is appended automatically and the configuration file
    is searched relative to the project root directory unless an absolute path
    is given.

``--no-attic``
    Do not move checkout workspace to attic if inline SCM switching is not possible.
    Instead a build error is issued.

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
:ref:`bob-init(1) <manpage-bob-init>`
