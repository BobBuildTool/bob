bob-status
==========

.. only:: not man

   Name
   ----

   bob-status - Show SCM status

Synopsis
--------

::

    bob status [-h] [--develop | --release] [-r] [-D DEFINES]
               [-c CONFIGFILE] [-e NAME] [-E] [-v]
               packages [packages ...]

Description
-----------

Show SCM status

Options
-------

``-c CONFIGFILE``
    Use config File

``-D DEFINES``
    Override default environment variable

``--develop``
    Use developer mode

``-e NAME``
    Preserve environment variable

``-E``
    Preserve whole environment

``-r, --recursive``
    Recursively display dependencies

``--release``
    Use release mode

``-v, --verbose``
    Increase verbosity (may be specified multiple times)

