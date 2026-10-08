Glossary
========

.. glossary::

   Variant-Id
        Describes *how* a particular :term:`Step` of a :term:`Package` is
        built. This includes all scripts, environment variables and recursive
        dependencies. See :ref:`concepts-implicit-versioning`.

   Build-Id
        Describes *what* is actually being built. This takes into account the
        actual sources of the checkout step, the build- and package-step scripts
        and recursively all depenencies. It identifies the (expected) build
        result and is used as index key of binary artifacts. See
        :ref:`concepts-implicit-versioning`.

   Recipe
        Yaml file holding the blueprint of what packages are built. Can inherit
        from :term:`classes <Class>` to reduce redundancy. When parsing the
        recipe graph one or more :term:`packages <Package>` are created from a
        single recipe.

   Package
        Particular instance of a recipe after resolving all inputs of the recipe
        (environment, tools, sandbox) and all dependencies.

   Class
        Syntactically the same as a recipe. Used to factor out common parts
        of recipes to remove redundancy.

   Step
        One of the three individual build steps of a recipe/package that are
        defined in Bob: checkout-, build- and package-step.

   Package-Id
        Describes *how* a :term:`Package` is built. Identical to the
        :term:`Variant-Id` of the package :term:`Step`.

   Upstream
        Relative direction in the dependency graph towards the dependencies of
        a recipe. The dependencies of a recipe are its upstream recipes.

   Downstream
        Relative direction in the dependency graph towards the consumers of a
        recipe. The recipes that depend on a recipe are its downstream recipes.

   Declared dependency
        A dependency that is explicitly named in the
        :ref:`configuration-recipes-depends` section of a recipe. Also called
        *direct* dependency. See :ref:`configuration-principle-dependencies`.

   Provided dependency
        A dependency that a recipe offers to its :term:`downstream <Downstream>`
        recipes by listing it in :ref:`configuration-recipes-providedeps`.
        Always refers to the providing side. See
        :ref:`configuration-principle-dependencies`.

   Injected dependency
        A dependency that a recipe receives because it was provided by one of
        its :term:`declared dependencies <Declared dependency>`. It is not named
        in the receiving recipe. See
        :ref:`configuration-principle-dependencies`.

   Ambient dependency
        A tool, sandbox or interpreter that a recipe inherits from its
        :term:`downstream <Downstream>` recipes. See
        :ref:`configuration-principle-dependencies`.

   Transitive dependency
        A package that is reachable by recursively following the dependencies
        of a package. See :ref:`configuration-principle-dependencies`.
