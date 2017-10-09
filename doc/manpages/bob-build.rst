.. _manpage-build:

bob-build
=========

.. only:: not man

   Name
   ----

   bob-build - Bob release mode build

Synopsis
--------

::

    bob build [-h] [--destination DEST] [-f] [-n] [-p] [--without-provided]
              [-b | -B | --normal] [--clean | --incremental]
              [--always-checkout RE] [--resume] [-q] [-v] [--no-logfiles]
              [-D DEFINES] [-c CONFIGFILE] [-e NAME] [-E] [--upload]
              [--download MODE] [--sandbox | --no-sandbox]
              [--clean-checkout]
              PACKAGE [PACKAGE ...]

Description
-----------

The *bob build* command is building packages locally in release mode.

.. include:: bob-build-dev.rst
