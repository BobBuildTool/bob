.. _manpage-bob-query-recipe:

bob-query-recipe
================

.. only:: not man

   Name
   ----

   bob-query-recipe - Query package sources

Synopsis
--------

::

    bob query-recipe [-h] [-D DEFINES] [-c CONFIGFILE]
                     [--sandbox | --slim-sandbox | --dev-sandbox | --strict-sandbox | --no-sandbox]
                     package

Description
-----------

Query recipe and class files of package.

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

``--sandbox``
    Enable partial sandboxing.

``--slim-sandbox``
    Enable slim sandboxing.

``--strict-sandbox``
    Enable strict sandboxing.
