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
                [--resume] [-n]
                [projectGenerator] [package] ...


Description
-----------

Generate Project Files.

Options
-------

``-c CONFIGFILE``
    Use config File

``-D DEFINES``
    Override default environment variable

``-e NAME``
    Preserve environment variable

``-E``
    Preserve whole environment

``--list``
    List available Generators

``-n``
    Do not build (bob dev) before generate project Files. RunTargets may not
    work

``--resume``
    Resume build where it was previously interrupted

Eclipse CDT project generator
-----------------------------

::

    bob project eclipse-cdt <package> [-h] [-u] [--buildCfg BUILDCFG] [--overwrite]
                            [--destination DEST] [--name NAME]
                            [--exclude EXCLUDES] [-I ADDITIONAL_INCLUDES]

The Eclipse CDT generator has the following specific options. They have to be
passed on the command line *after* the package name.

``--buildCfg BUILDCFG``
    Adds a new buildconfiguration. Format: <Name>::<flags>

``--destination DEST``
    Destination of project files

``--exclude EXCLUDES``
    Packages will be marked as 'exclude from build' in eclipse. Usefull if indexer runs OOM.

``-I ADDITIONAL_INCLUDES``
    Additional include directories. (added recursive starting from this directory)

``--name NAME``
    Name of project. Default is complete_path_to_package

``--overwrite``
    Remove destination folder before generating.

``-u, --update``
    Update project files (.project)


QtCreator project generator
---------------------------

::

    bob project qt-project <package> [-h] [-u] [--buildCfg BUILDCFG] [--overwrite]
                           [--destination DEST] [--name NAME]
                           [-I ADDITIONAL_INCLUDES] [-f Filter] [--exclude Excludes] [--kit KIT]

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

``-I ADDITIONAL_INCLUDES``
    Additional include directories. (added recursive starting from this directory)

``--kit KIT``
    Kit to use for this project

``--name NAME``
    Name of project. Default is complete_path_to_package

``--overwrite``
    Remove destination folder before generating.

``-u, --update``
    Update project files (.files, .includes)

