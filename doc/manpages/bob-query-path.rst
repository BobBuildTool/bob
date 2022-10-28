.. _manpage-bob-query-path:

bob-query-path
==============

.. only:: not man

   Name
   ----

   bob-query-path - Query path information

Synopsis
--------

::

    bob query-path [-h] [-f FORMAT] [-D DEFINES] [-c CONFIGFILE]
                   [--sandbox | --no-sandbox] [--develop | --release]
                   [-q] [--fail] PACKAGE [PACKAGE ...]

Description
-----------

This command lists existing workspace directory names for packages given
on the command line. Output is formatted with a format string that can
contain placeholders

    +----------+------------------+
    |{name}    |package name      |
    +----------+------------------+
    |{src}     |checkout directory|
    +----------+------------------+
    |{build}   |build directory   |
    +----------+------------------+
    |{dist}    |package directory |
    +----------+------------------+

The default format is '{name}<tab>{dist}'.

If a directory does not exist for a step (because that step has never
been executed or does not exist) or if one or more of the given packages
does not exist, a error message is printed unless the ``-q`` option is
provided.

Options
-------

``-c CONFIGFILE``
    Use config File

``-D DEFINES``
    Override default environment variable

``--develop``
    Use developer mode

``-f FORMAT``
    Output format string

``-q``
    Be quiet in case of errors

``--fail``
    Return a non-zero exit code in case of errors

``--no-sandbox``
    Disable sandboxing

``--release``
    Use release mode

``--sandbox``
    Enable sandboxing

