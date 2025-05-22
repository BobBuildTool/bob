.. _manpage-ls-recipes:

bob-ls-recipes
==============

.. only:: not man

   Name
   ----

   bob-ls-recipes - List all known recipes

Synopsis
--------

::

    bob ls-recipes [-h] [-D DEFINES] [-c CONFIGFILE]
                   [--sandbox | --slim-sandbox | --dev-sandbox | --strict-sandbox | --no-sandbox]
                   [--all | --used | --orphaned] [--sources]


Description
-----------

List known recipes. In contrast to the :ref:`manpage-bob-ls` command, which works
on packages, this command works on recipes.

By default, all found recipes are printed (``--all``). Because the recipe YAML
files can declare more than one recipe (see
:ref:`configuration-recipes-multipackage`), the number of listed recipes is
usually bigger than the number of YAML files. Add the ``--sources`` option too
see which file declared what recipe.

Note that listing used or orphaned recipes very much depends on the project
configuration. Especially when using layers, there may be a large number of
unused recipes.

Options
-------

``--all``
    List all recipes (default).

``-c CONFIGFILE``
    Use config File

``--dev-sandbox``
    Enable development sandboxing.

``-D DEFINES``
    Override default environment variable

``--no-sandbox``
   Disable sandboxing.

``--orphaned``
   List recipes that are unused.

``--sandbox``
   Enable partial sandboxing.

``--slim-sandbox``
    Enable slim sandboxing.

``--sources``
    Print source YAML file names. This includes the recipe and all inherited
    classes. The file names are separated by TAB character.

``--strict-sandbox``
    Enable strict sandboxing.

``--used``
    List all used recipes. These are recipes that are referenced directly or
    indirectly by a root package.
