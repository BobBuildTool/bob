.. _manpage-bob-project:

bob-project
===========

.. only:: not man

   Name
   ----

   bob-project - Create IDE project files

Synopsis
--------

::

    bob project [-h] [--list] [-D DEFINES] [-c CONFIGFILE] [-e NAME] [-E]
                [--download MODE] [--resume] [-n] [-b] [-j [JOBS]]
                [--sandbox | --slim-sandbox | --dev-sandbox | --strict-sandbox | --no-sandbox]
                [projectGenerator] [package] ...


Description
-----------

Generate Project Files.

Options
-------

``-c CONFIGFILE``
    Use config File

``--dev-sandbox``
    Enable development sandboxing.

    Always build packages in an isolated environment where only declared
    dependencies are visible. If a sandbox image is available, it is used.
    Otherwise the host paths are made read-only.

``--download MODE``
    Download from binary archive (yes, no, deps, packages)

    See :ref:`bob-dev(1) <manpage-dev>` for details.

``-D DEFINES``
    Override default environment variable

``-e NAME``
    Preserve environment variable

``-E``
    Preserve whole environment

``-j, --jobs``
    Specifies the number of jobs to run simultaneously.

``--list``
    List available Generators

``-n``
    Do not build (bob dev) before generate project Files. RunTargets may not
    work

``--no-sandbox``
    Disable sandboxing. This is the default.

``-b``
    Do build only (bob dev -b) before generate project Files. No checkout

``--resume``
    Resume build where it was previously interrupted

``--sandbox``
    Enable partial sandboxing.

    Build packages in an ephemeral container if a sandbox image is available
    for the package. Inside the sandbox, stable execution paths are used. In
    absence of a sandbox image, no isolation is performed.

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

Eclipse CDT project generator
-----------------------------

::

    bob project eclipseCdt <package> [-h] [-u] [--buildCfg BUILDCFG] [--overwrite]
                            [--destination DEST] [--name NAME]
                            [--exclude EXCLUDES] [-I ADDITIONAL_INCLUDES]

The Eclipse CDT generator has the following specific options. They have to be
passed on the command line *after* the package name.

``--buildCfg BUILDCFG``
    Adds a new buildconfiguration. Format: <Name>::<flags>. Flags are passed
    to bob dev. See bob dev for a list of availabe flags.

``--destination DEST``
    Destination of project files.

``--exclude EXCLUDES``
    Packages will be marked as 'exclude from build' in eclipse. Usefull if indexer runs OOM.

``-I ADDITIONAL_INCLUDES``
    Additional include directories.

``--name NAME``
    Name of project. Default is complete_path_to_package

``--overwrite``
    Remove destination folder before generating.

``-u, --update``
    Update project files (.project).


QtCreator project generator
---------------------------

::

    bob project qt-project <package> [-h] [-u] [--buildCfg BUILDCFG] [--overwrite]
                           [--destination DEST] [--name NAME]
                           [-I ADDITIONAL_INCLUDES] [-f Filter]
                           [--exclude Excludes] [--include Includes] [--kit KIT]
                           [-S START_INCLUDES] [-C CONFIG_DEF]

This generator also supports generation of project files for native Windows QtCreator 
by using MSYS2. The prerequisite is, that MSYS2 must be started by msys2_shell.cmd script.

The QtCreator project generator has the following specific options. They have
to be passed on the command line *after* the package name.

``--buildCfg BUILDCFG``
    Adds a new buildconfiguration. Format: <Name>::<flags>

``--destination DEST``
    Destination of project files

``-f Filter, --filter Filter``
    File filter. A regex for matching additional files.

``--exclude Excludes``
    Package filter. A regex for excluding packages in QTCreator.

``--include Includes``
    Include package filter. A regex for including only the specified packages in QTCreator.
    Use single quotes to specify your regex. For exmaple: --include 'foobar-.*'
    You can also mix the Includes with the Excludes. In this case always use the Includes option beforehand.
    For example: --include 'foobar-.*' --exclude 'foobar-baz' This will ensure you only include packages
    wtih foobar-* but excludes the foobar-baz package.

``-I ADDITIONAL_INCLUDES``
    Additional include directories.

``--kit KIT``
    Name of the kit to use for this project.

    Qt Creator usually auto-detects your installed compilers on the system and
    creates one or more "kits" based on the detected settings. Bob will use the
    "Desktop" kit by default. The generator cannot create a project if
    QtCreator is not installed. If the "Desktop" kit is missing you have to
    create one or specify an existing one with the ``--kit`` option.

    See the online documentation [#l1]_ for more information.

``--name NAME``
    Name of project. Default is complete_path_to_package

``--overwrite``
    Remove destination folder before generating.

``-u, --update``
    Update project files (.files, .includes, .config)

``-S START_INCLUDES``
    Additional include directories, will be placed at the beginning of the include list.

``-C CONFIG_DEF``
    Add line to .config file. Can be used to specify preprocessor defines used by the QTCreator.


.. _manpage-project-vscode:

Visual Studio Code project generator
------------------------------------

::

    bob project vscode <package> [-h] [--name NAME] [--destination DEST]
                       [--exclude EXCLUDES]
                       [--include INCLUDE] [-I ADDITIONAL_INCLUDES]
                       [-S START_INCLUDES] [--sort]

The Visual Studio Code generator will generate a single .code-workspace file which could be opened in the Visual Studio Code. 

The Visual Studio Code generator has the following specific options. They have to be
passed on the command line *after* the package name.

``--name NAME``
    Name of project. Default is package_name

``--destination DEST``
    Destination of project files.

``--exclude EXCLUDES``
    Package filter. A regex for excluding packages in VSCode.

``--include INCLUDE``
    Include package filter. A regex for including only the specified packages in VSCode.
    Use single quotes to specify your regex. For exmaple: --include 'foobar-.*'
    You can also mix the Includes with the Excludes. In this case always use the Includes option beforehand.
    For example: --include 'foobar-.*' --exclude 'foobar-baz' This will ensure you only include packages
    wtih foobar-* but excludes the foobar-baz package.

``-I ADDITIONAL_INCLUDES``
    Additional include directories.

``-S START_INCLUDES``
    Additional include directories, will be placed at the beginning of the include list.

``--sort``
    Sort the dependend packages by name (default: unsorted)


External links
--------------

.. [#l1] https://doc.qt.io/qtcreator/creator-configuring.html#checking-build-and-run-settings
