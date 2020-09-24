.. _manpage-show:

bob-show
========

.. only:: not man

   Name
   ----

   bob-show - Show properties of a package

Synopsis
--------

::

    bob show [-h] [-D DEFINES] [-c CONFIGFILE] [--sandbox | --no-sandbox]
             [--show-empty] [--show-common] [--indent INDENT | --no-indent]
             [--format {yaml,json,flat,diff}]
             [-f {buildNetAccess,buildTools,buildToolsWeak,buildVars,buildVarsWeak,
                  checkoutAssert,checkoutDeterministic,checkoutSCM,checkoutTools,
                  checkoutToolsWeak,checkoutVars,checkoutVarsWeak,depends,
                  fingerprintIf,jobServer,meta,metaEnvironment,packageNetAccess,
                  packageTools,packageToolsWeak,packageVars,packageVarsWeak,
                  relocatable,root,sandbox,scriptLanguage,shared}]
             packages [packages ...]


Description
-----------

Show properties of one or more packages. Most properties are the same that
are specified in the recipes. This command will show the final values of the
properties that are used at build time. Additionally to the recipe properties
the following synthetic properties are shown:

* ``meta``: Map of sub-properties that describe the package

   * ``deterministic``: True if Bob thinks the package is fully reproducible.
     False if some input of the package is variable.
   * ``name``: The package name
   * ``package``: The package path
   * ``recipe``: The recipe name that created this package
   * ``checkoutVariantId``: The variant-Id of the package step. Different
     checkout variant-Ids are always checked out separately.
   * ``buildVariantId``: The variant-Id of the build step.
   * ``packageVariantId``: The variant-Id of the package. See
     :ref:`implicit versioning concepts <concepts-implicit-versioning>` for
     more details.

* ``sandbox``: The sandbox package that is used for this package.

By default the information is shown as YAML document. If multiple packages are
selected then multiple YAML documents are generated too. The output format can
be controlled with the ``--format`` option. The ``flat`` format is a custom
output format where each key/value pair is printed on a separate line.

The ``diff`` output mode is special because it compares two packages and shows
the properties that are different between both packages.

Options
-------

``-f FIELD``
   Show only the given ``FIELD``. This option may be specified more than once
   to show additional fields. If this option is not given then all fields are
   show.

``--format {yaml,json,flat,diff}``
   Selects a different output format. Defaults to ``yaml``.

``--indent INDENT``
   The ``yaml`` and ``json`` output formats are pretty printed with an
   indentation of 4 spaces by default. Use this option to chance this to
   ``INDENT`` number of spaces.

``--no-indent``
   Disables the pretty printing of the ``yaml`` and ``json`` formats. The
   output will be more compact but is less readable.

``--no-sandbox``
   Disable sandboxing.

``--sandbox``
   Enable sandboxing.

``--show-common``
   The ``diff`` format suppresses identical fields by default. This option
   forces them to be shown.

``--show-empty``
   By default empty or disabled properties are filtered out to keep the output
   as short as possible. Specifying this option will still show them. If the
   package does not have a checkout- or build-step the respective properties do
   not exist and will never be shown.
