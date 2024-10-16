
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

    bob query-meta [-h] [-D DEFINES] [-c CONFIGFILE] [-r]
                   [--sandbox | --slim-sandbox | --dev-sandbox | --strict-sandbox | --no-sandbox]
                   packages [packages ...]

Description
-----------

This command lists variables from the metaEnvironment section of the recipe.

Options
-------

``-c CONFIGFILE``
    Use config File

``-D DEFINES``
    Override default environment variable

``--dev-sandbox``
    Enable development sandboxing.

``--no-sandbox``
    Disable sandboxing

``-r``
    Also list metaEnvironment variables for all dependencies.

``--sandbox``
    Enable partial sandboxing.

``--slim-sandbox``
    Enable slim sandboxing.

``--strict-sandbox``
    Enable strict sandboxing.
