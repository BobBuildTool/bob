bob-ls
======

.. only:: not man

   Name
   ----

   bob-ls - List package hierarchy

Synopsis
--------

::

    bob ls [-h] [-a] [-r] [-p] [-D DEFINES] [-c CONFIGFILE] 
           [--sandbox | --no-sandbox] [package]


Description
-----------

List packages.

Options
-------

``-a, --all``
    Show indirect dependencies too

``-c CONFIGFILE``
    Use config File

``-D DEFINES``
    Override default environment variable

``--no-sandbox``
    Disable sandboxing

``-p, --prefixed``
    Prints the full path prefix for each package

``-r, --recursive``
    Recursively display dependencies

``--sandbox``
    Enable sandboxing

