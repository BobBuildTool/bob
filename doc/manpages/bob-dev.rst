bob-dev
=======

.. only:: not man

   Name
   ----

   bob-dev - Bob develop mode build

Synopsis
--------

::

    bob dev [-h] [--destination DEST] [-f] [-n] [-b | -B]
            [--clean | --incremental] [--resume] [-q] [-v] [-D DEFINES]
            [-c CONFIGFILE] [-e NAME] [-E] [--upload] [--download MODE]
            [--sandbox | --no-sandbox] [--clean-checkout]
            PACKAGE [PACKAGE ...]

Description
-----------

The *bob dev* command is building packages locally in develop mode. This mode
is intended to be used by developers to incrementally build the packages. 

.. include:: bob-build-dev.rst
