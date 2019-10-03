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
                [--sandbox | --no-sandbox]
                [projectGenerator] [package] ...


Description
-----------

Generate Project Files.

Options
-------

``-c CONFIGFILE``
    Use config File

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
    Disable sandboxing

``-b``
    Do build only (bob dev -b) before generate project Files. No checkout

``--resume``
    Resume build where it was previously interrupted

``--sandbox``
    Enable sandboxing

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
    Additional include directories. (added recursive starting from this directory)

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
    Additional include directories. (added recursive starting from this directory)

``--kit KIT``
    Kit to use for this project

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
