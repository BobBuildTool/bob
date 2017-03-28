
.. _manpage-query-meta:

bob-query-meta
==============

.. only:: not man

   Name
   ----

   bob-query-meta - Query metaEnvironment variables

Synopsis
--------

::

    bob query-meta [-h] [-D DEFINES] [-c CONFIGFILE] [-r] PACKAGE [PACKAGE ...]

Description
-----------

This command lists variables from the metaEnvironment section of the recipe.

Options
-------

``-c CONFIGFILE``
    Use config File

``-D DEFINES``
    Override default environment variable

``-r``
    Also list metaEnvironment variables for all dependencies.
