.. highlight:: yaml

.. _configuration:

Configuration
=============

When building packages, Bob executes the instructions defined by the recipes.
All recipes are located relative to the project root directory in the ``recipes``
subdirectory. Recipes are YAML files with a defined structure. The name of the
recipe and the resulting package(s) is derived from the file name by removing
the trailing '.yaml'. To aid further organization of the recipes, they may be
put into subdirectories under ``recipes``. The directory name becomes part of the
package name with a ``::``-separator.

To minimize repetition of common functionality, there is also an optional
``classes`` subdirectory.  Classes have the same structure as recipes and can
be included from recipes and other classes to factor out common stuff. Files
that do not have the '.yaml' extension are ignored when parsing the recipes and
classes directories.

In case there needs to be a choice between multiple recipes that provide
interchangeable packages, aliases can be defined that resolve to other recipes.
The optional ``aliases`` directory holds YAML files that declare alias names.
The naming structure is the same as in the ``recipes`` and ``classes``
directory. This is usually known as virtual packages in other build systems.

There are two additional configuration files: ``config.yaml`` and
``default.yaml``. The former contains static configuration options while the
latter holds some options that can have a default value and might be overridden
on the command line. Putting that all together, a typical recipe tree looks like
the following:

.. code-block:: none

    .
    ├── aliases
    │   └── kernel.yaml
    ├── classes
    │   └── make.yaml
    ├── config.yaml
    ├── default.yaml
    └── recipes
        ├── busybox.yaml
        ├── initramfs
        │   ├── inittab
        │   └── rcS
        ├── initramfs.yaml
        ├── linux.yaml
        ├── toolchain
        │   ├── arm-linux-gnueabihf.yaml
        │   ├── make.yaml
        │   └── x86.yaml
        └── vexpress.yaml

Such a recipe and configuration tree is meant to be handled by an SCM (Source Code Manager).
As you can see in the above tree there is a ``toolchain`` subdirectory in the recipes.
The packages in this directory will be named
``toolchain::arm-linux-gnueabihf``, ``toolchain::make`` and ``toolchain::x86``.
You can also see that there are other files (initramfs/...) that are included
by recipes but are otherwise ignored by Bob.

In addition to the single project tree configuration, Bob supports layers. A
layer has the same structure as shown above but is merged with the other layers
during parsing. This structure is recursive (a layer may contain another layer)
but is flattened during parsing. A typical structure might look like the
following:

.. code-block:: none

    .
    ├── classes
    │   └── ...
    ├── config.yaml
    ├── default.yaml
    ├── layers
    │   ├── bsp
    │   │   ├── default.yaml
    │   │   └── recipes
    │   │       ├── linux.yaml
    │   │       └── toolchain
    │   │           └── arm-linux-gnueabihf.yaml
    │   └── myapp
    │       └── recipes
    │           └── fancy-app.yaml
    └── recipes
        ├── busybox.yaml
        ├── initramfs
        │   ├── inittab
        │   └── rcS
        ├── initramfs.yaml
        ├── toolchain
        │   ├── make.yaml
        │   └── x86.yaml
        └── vexpress.yaml

Conceptually the recipes (and classes) from the ``bsp`` and ``myapp`` layers
are parsed together at the same level with the recipes of the project root. The
recipes may reference each other freely. The only restriction is that a
particular recipe or class must only be defined by one layer. It is not allowed
to "overwrite" a recipe in a layer above.

Layers must be configured in ``config.yaml`` to be picked up. This is a list of
either simple layer names that are then expected in the ``layers`` directory or
an SCM specification where the layer is fetched from::

    layers:
        - myapp
        - name: bsp
          scm: git
          url: git@github.com:...

The order of layers is important with respect to settings made by
``default.yaml`` in the various layers. The project root has the highest
precedence. The layers in ``config.yaml`` are named from highest to lowest
precedence. A layer with a higher precedence can override settings from layers
of lower precedence.

Layers allow you to structure your projects into larger entities that can be
reused in other projects. This modularity helps to separate different aspects
of bigger projects like the used toolchain, the board support package and the
applications integration.

Principle operation
-------------------

All packages are built by traversing the recipe tree starting from one or more
root recipes. These are recipes that have the ``root`` attribute set to
``True``. There must be at least one root recipe in a project. The tree of
recipes is traversed depth first. While following the dependencies, Bob keeps a
local state that consists of the following information:

Environment
    Bob always keeps the full set of variables but only a subset is visible
    when executing the scripts. Initially, only the variables defined in
    ``default.yaml`` in the ``environment`` section are available. Environment
    variables can be set at various points that are described below in more
    detail.

Tools
    Tools are aliases for paths to executables. Initially there are no tools.
    They are defined by ``provideTools`` and must be explicitly imported by
    downstream recipes by listing ``tools`` in the ``use`` attribute. Like
    environment variables, the tools are kept as key value pairs where the key
    is a string and the value is the executable and library paths that are
    imported when using a tool.

Sandbox
    This defines the root file system and paths that are used to build the
    package.  Unless a sandbox is consumed by listing ``sandbox`` in the
    ``use`` attribute of a dependency, the normal host executables are used.
    Sandboxed builds are described in a separate section below.

All of this information is carried as local state when traversing the
dependency tree. Each recipe gets a local copy that is propagated upstream.
Any updates to downstream recipes must be done by explicitly offering the
information with one of the ``provide*`` keywords and the downstream recipe must
consume it by adding the relevant item to the ``use`` attribute of the
dependency.

Step execution
~~~~~~~~~~~~~~

The actual work when building a package is done in the following three steps.
They are scripts that are executed with (and only with) the declared
environment and tools.

Checkout
    The checkout step is there to fetch the source code or any external input
    of the package. Despite the script defined by ``checkoutScript``, Bob
    supports a number of source code management systems natively. They can be
    listed in ``checkoutSCM`` and are fetched/updated before the
    ``checkoutScript`` is run.

Build
    This is the step where most of the work should be done to build the
    package. The ``buildScript`` receives the result of the checkout step as
    argument ``$1``, and any further dependency whose result is consumed is
    passed in order starting with ``$2``. If no checkout step was provided,
    ``$1`` will point to some invalid path.

Package
    Typically, the build step will produce a lot of intermediate files (e.g.
    object files). The package step has the responsibility to distill a clean
    result of the package. The ``packageScript`` will receive a single argument
    with the path to the build step.

Each step of a recipe is executed separately and always in the above order. The
scripts' working directory is already where the result is expected. The scripts
should make no assumption about the absolute path or the relative path to other
steps. Only the working directory might be modified.

Script languages
~~~~~~~~~~~~~~~~

Bob itself is written in the Python scripting language but actually independent
of the scripting language that is used during step execution (see above).
Currently Bob supports two scripting languages: bash and PowerShell. Classes
and recipes may define their scripts in one or both scripting languages. The
actually used language at build time is determined by the
:ref:`configuration-recipes-scriptLanguage` key or, if nothing was specified,
by the project :ref:`configuration-config-scriptLanguage` setting. The other
language scripts are ignored.

Environment handling
~~~~~~~~~~~~~~~~~~~~

The variables listed in :ref:`configuration-config-environment` of
``default.yaml`` with their configured value are mangled through
:ref:`configuration-principle-subst` by the current OS environment and are then
taken over into the initial environment. The user might additionally override
or set certain variables from the command line. Such variables are always taken
over verbatim. The so calculated set of variables is the starting point for
each root recipe.

The next steps are repeated for each recipe as the dependency tree is traversed.
A copy of the environment is inherited from the downstream recipe.

1. Any variable defined in ``environment`` is set to the given value.
2. Make a copy of the local environment that is subsequently passed to each
   dependency (named "forwarded environment" thereafter).
3. For each dependency do the following:

   a. Make a dedicated copy of the environment for the dependency.
   b. Set variables given in the ``environment`` attribute of the dependency
      in this copy.
   c. Descend to the dependency recipe with that environment.
   d. Merge all variables of the ``provideVars`` section of the dependency
      into the local environment if ``environment`` is listed in the ``use``
      attribute of the dependency.
   e. If the ``forward`` attribute of the dependency is ``True`` then any
      merged variable of the previous step is updated in the forwarded
      environment too.

After all dependencies have been processed, the environment variables of tools
(see :ref:`configuration-recipes-provideTools`) that are used in the recipe are
merged into the local environment. Finally, variables defined in
:ref:`configuration-recipes-privateenv` and
:ref:`configuration-recipes-metaenv` are merged too.

A subset of the resulting local environment can be passed to the three
execution steps. The variables available to the scripts are defined by
:ref:`configuration-recipes-vars` and :ref:`configuration-recipes-vars-weak`.
The former property defines variables that are considered to influence the
build while the latter names variables that are expected to *not* influence the
outcome of the build.

A variable that is consumed in one step is also set in the following. This
means a variable consumed through checkoutVars is also set during the build and
package steps. Likewise, a variable consumed by buildVars is set in the package
step too. The rationale is that all three steps form a small pipeline. If a
step depends on a certain variable then the result of the following step is
already indirectly dependent on this variable. Thus it can be set during the
following step anyway.

A recipe might optionally offer some variables to the downstream recipe with a
``provideVars`` section. The values of these variables might use variable
substitution where the substituted values are coming from the local
environment. The downstream recipe must explicitly consume these provided
variables by adding ``environment`` to the ``use`` attribute of the dependency.

Tool handling
~~~~~~~~~~~~~

Tools are handled very similar to environment variables when being passed in
the recipe dependency tree. Tools are aliases for a package together with a
relative path to the executable(s) and optionally some library paths for shared
libraries. Another recipe using a tool gets the path to the executable(s) added
to its ``$PATH``.

Starting at the root recipe, there are no tools. The next steps are repeated
for each recipe as the dependency tree is traversed. A copy of the tool
aliases is inherited from the downstream recipe.

#. Make a copy of the local tool aliases that is subsequently passed to each
   dependency (named "forwarded tools" thereafter).
#. For each dependency do the following:

   a. Descend to the dependency recipe with the forwarded tools
   b. Merge all tools of the ``provideTools`` section of the dependency into
      the local tools if ``tools`` is listed in the ``use`` attribute of the
      dependency.
   c. If the ``forward`` attribute of the dependency is ``True`` then any
      merged tools of the previous step are updated in the forwarded tools too.

While the full set of tools is carried through the dependency tree, only a
specified subset of these tools is available when executing the steps of a
recipe.  The available tools are defined by {checkout,build,package}Tools. A
tool that is consumed in one step is also set in the following. This means a
tool consumed through checkoutTools is also available during the build and
package steps. Likewise, a tool consumed by buildTools is available in the
package step too.

To define one or more tools, a recipe must include a ``provideTools`` section
that defines the relative execution path and library paths of one or more tool
aliases. These aliases may be picked up by the downstream recipe by having
``tools`` in the ``use`` attribute of the dependency.

Sandbox operation
~~~~~~~~~~~~~~~~~

Unless a sandbox is configured for a recipe, the steps are executed directly on
the host. Bob adds any consumed tools to the front of ``$PATH`` and controls
the available environment variables. Apart from this, the build result is pretty
much dependent on the installed applications of the host.

Initially no sandbox is defined. An upstream recipe might offer its built
package as sandbox through ``provideSandbox``. The downstream recipe must define
``sandbox`` in the ``use`` attribute of this dependency to pick it up as
sandbox. This sandbox is effective only for the current recipe. If ``forward``
is additionally set to ``True`` the following dependencies will inherit this
sandbox for their execution.

The sandbox image must provide everything to execute the steps. In particular,
the following things must be provided by the sandbox image:

* There must be an ``etc/passwd`` file containing the "nobody" user with uid
  65534.
* There must *not* be a ``home`` directory. Bob creates this directory on
  demand and will fail if it already exists.
* There must *not* be a ``tmp`` directory for the same reason.
* The interpreter of the used script language must be available (``bash`` or
  ``pwsh``) and it must be in ``$PATH``. When using bash (the default) at
  least version 4 must be installed. Bob uses associative arrays that are not
  available in earlier versions.

.. _configuration-principle-subst:

String substitution
~~~~~~~~~~~~~~~~~~~

At most places where strings are handled in keywords, it is possible to use
variable substitution. These substitutions might be simple variables, but a
variety of string processing functions is also available that can optionally be
extended by plugins. The following syntax is supported:

* Variable substitution
    * ``${var}``: The value of ``var`` is substituted. The variable has to be
      defined or an error will be raised. The braces can be omitted if the
      variable name consists only of letters, numbers and ``_`` and when the
      name is followed by a character that is not interpreted as part of its
      name.
    * ``${var:-default}``: If variable ``var`` is unset or null, the expansion
      of ``default`` is substituted. Otherwise the value of ``var`` is
      substituted. Omitting the colon results in a test only for ``var`` being
      unset.
    * ``${var:+alternate}``: If variable ``var`` is unset or null, nothing is
      substituted. Otherwise the expansion of ``alternate`` is substituted.
      Omitting the colon results in a test only for ``var`` being unset.
* ``$(fun,arg1,...)``: Substitutes the result of calling ``fun`` with the given
  arguments. Unlike unix shells, which employ word splitting at whitespaces, the
  function arguments are separated by commas. Any white spaces are kept and belong
  to the arguments. To put a comma or closing parenthesis into an argument it has to
  be escaped by a backslash or double/single quotes.
* Quoting
    * ``"..."``: Double quotes begin a new substitution context that runs until
      the matching closing double quote. All substituions are still recognized.
    * ``'...'``: Enclosing characters in single quotes preserves the literal
      value of each character within the quotes.  A single quote may not occur
      between single quotes, even when preceded by a backslash.
    * ``\.``: A backslash preserves the literal meaning of the following
      character. The only exception is within single quotes where backslash is
      not recognized as meta character.

The following built in string functions are supported:

* ``$(eq,left,right)``: Returns ``true`` if the expansions of ``left`` and
  ``right`` are equal, ``false`` otherwise.
* ``$(match,string,pattern[,flags])``: Returns ``true`` if ``pattern`` is found
  in ``string``, ``false`` otherwise. Quoting the pattern is recommended. Flags
  are optional. The only currently supported flag is ``i`` to ignore case while
  searching.
* ``$(if-then-else,condition,then,else)``: The expansion of ``condition`` is
  interpreted as a boolean value. If the condition is true the expansion of
  ``then`` is returned. Otherwise ``else`` is returned.
* ``$(is-sandbox-enabled)``: Return ``true`` if a sandbox image is used in the
  current context, ``false`` otherwise.
* ``$(is-tool-defined,name)``: If ``name`` is a defined tool in the current
  context the function will return ``true``. Otherwise ``false`` is returned.
* ``$(ne,left,right)``: Returns ``true`` if the expansions of ``left`` and
  ``right`` differ, otherwise ``false`` is returned.
* ``$(not,condition)``: Interpret the expansion of ``condition`` as boolean
  value and return the opposite.
* ``$(or,condition1,condition2,...)``: Expand each condition and then interpret
  each condition as boolean.  Return ``false`` when all conditions are false, otherwise
  ``true``.
* ``$(and,condition1,condition2,...)``: Expand each condition and the interpret
  each condition as booelan. Return ``true`` when all conditions are true,
  otherwise ``false``.
* ``$(strip,text)``: Remove leading and trailing whitespaces from the expansion
  of ``text``.
* ``$(subst,from,to,text)``: Replace every occurence of ``from`` with ``to`` in
  ``text``.
* ``$(get-tool-env,tool,var[,default])``: Return the environment variable ``var``
  defined by tool ``tool``. The substition will fail if the variable is
  undefined in the tools :ref:`configuration-recipes-provideTools` environment
  definition unless the optional ``default`` is given, which is then used
  instead.

The following built in string functions are additionally supported in
:ref:`package path queries <manpage-bobpaths>`. They cannot be used in recipes
as they work on packages:

* ``$(matchScm,property,pattern)``: Return ``true`` if there is at least one
  :ref:`configuration-recipes-scm` in the package that has a ``property`` that
  matches the ``pattern``. Otherwise returns ``false``. Shell globbing patterns
  may be used as ``pattern``.

Plugins may provide additional functions as described in
:ref:`extending-hooks-string`.

.. _configuration-principle-booleans:

Boolean properties
~~~~~~~~~~~~~~~~~~

Depending on the context one or more of the following types are supported in
boolean properties:

String
  A string that is subject to
  :ref:`variable subsitution <configuration-principle-subst>`. The empty
  string, "0" (zero) and "false" (case insensitive) are considered as logical
  "false". Any other value is considered as "true".

Boolean
  A YAML boolean value. (``True``, ``False``)

IfExpression
  An IfExpression which is a special YAML-type (``!expr``) defined by Bob. This
  is an expression in infix notation that is using the same
  :ref:`bobpaths_string_literals` and :ref:`bobpaths_string_function_calls` as
  available for :ref:`manpage-bobpaths`. If the expression is a simple string
  it's value is interpreted as defined above for plain strings. More complex
  expressions are always of boolean type.  Example::

     if: !expr |
           "${FOO}" == "bar" || "${BAZ}"

The allowed type is specified at each property individually.

.. _configuration-principle-fingerprinting:

Host dependency fingerprinting
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Bob closely tracks the input of all packages. This includes all checked out
sources and the dependencies to other packages. If something is changed, Bob can
accurately determine which packages have to be rebuilt. This information is
also used to find matching binary artifacts. If a recipe depends on resources
that are outside of the declared recipes, the situation changes though. Bob
cannot infer what external resources are actually used and how these influence
the build result.

A common host dependency that "taints" the build result is the host compiler.
While the host compiler typically does not change, it limits the portability
across machines in the form of binary artifacts. The dependency on the host
architecture is obvious, but also the libc has to be considered. This can be
extended to other libraries that might be used by the recipe.

To let Bob know about the usage and state of an external host resource, a
fingerprint script can be used in the recipe. The output of the fingerprint
script is used to "tag" the created package. If the fingerprint changes, the
package is rebuilt. The fingerprint is also attached to the binary artifact.
To download a binary artifact of a package, the fingerprint has to match.

The fingerprint does not apply to the `checkoutScript`, though. If the result
of your `checkoutScript` depends on the host that it runs on, you have to set
:ref:`configuration-recipes-checkoutdeterministic` to `False`. The fingerprint
serves only as a virtual input to the build and package steps to declare to Bob
what part of the host is used by the recipe.

The impact of the host that is declared by a fingerprint script applies only to
the result of a recipe. Specifically, it does not apply to the implied
*behaviour* of any provided tools. This means that when using a tool from
another recipe that is directly or indirectly affected by a fingerprint, the
using recipe is not affected. The rationale for this exception of transitivity
is that it typically does not matter *where* a tool is built but how it
*behaves*.

See :ref:`configuration-recipes-fingerprintScript` and
:ref:`configuration-recipes-provideTools` for information where fingerprint scripts
can be configured.

Recipe and class keywords
-------------------------

.. _configuration-recipes-scripts:

{checkout,build,package}Script[{Bash,Pwsh}]
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Type: String

This is the script that is executed by Bob at the respective stage when
building the Packet. It is strongly recommended to write the script as a
newline preserving block literal. See the following example (note the pipe
symbol on the end of the first line)::

    buildScript: |
        $1/configure
        make

The suffix of the keyword determines the language of the script. Using the
``Bash`` suffix (e.g.  ``buildScriptBash``) defines a script that is
interpreted with ``bash``. Likewise, the ``Pwsh`` suffix (e.g.
``buildScriptPwsh``) defines a PowerShell script. Which language is used at
build time is determined by the :ref:`configuration-recipes-scriptLanguage` key
or, if nothing was specified, by the project
:ref:`configuration-config-scriptLanguage` setting. A keyword without a suffix
(e.g.  ``buildScript``) is interpreted in whatever language is finally used at
build time. If both the keyword with the build time language suffix and without
a suffix are present then the keyword with the build language suffix takes
precedence.

The script is subject to file inclusion with the ``$<<path>>``, ``$<@path@>``
and ``$<'path'>`` syntax. The files are included relative to the current
recipe.  The given ``path`` might be a shell globbing pattern. If multiple
files are matched by ``path``, the files are sorted by name before being
processed. Matching no file leads to an error. Depending on the particular
syntax, the file(s) are included in different ways:

``$<<path>>``
    This syntax concatenates the file(s) and replaces the escape pattern with a
    (possibly temporary) file name which has all the content. The script will
    always see a single file name.

``$<@path@>``
    For each matched file, the script will see a (possibly temporary) file with
    its content. The order of files is still sorted by the original file name.
    Like the ``$<<path>>`` syntax, the file names are not predictable at
    runtime and do not resemble the original file names.

``$<'path'>``
    This syntax concatenates the file(s) and inserts the result as string
    literal. The strings are fully quoted and *not* subject to any parameter
    substitution.

    .. note::
       When including files as quoted strings, they have to be UTF-8 encoded.

The scripts of any classes that are inherited which define
a script for the same step are joined in front of this script in the order the
inheritance is specified. The inheritance graph is traversed depth first and
every class is included exactly once.

Dependencies of the recipe are by default only available to the
``buildScript``. The path to the previous step (checkout workspace for
``buildScript``, build workspace for ``packageScript``) is always passed in
``$1``. Other dependencies are available in the order in which they were
declared at the :ref:`configuration-recipes-depends` section of the recipe. If
a dependencies ``checkoutDep`` flag is set to ``True`` it will also be
available to the ``checkoutScript``. This should be used carefully as it makes
the checkout of the recipe sources dependent on the result of another
dependency.

During execution of the script only the environment variables SHELL, USER,
TERM, HOME and anything that was declared via {checkout,build,package}Vars
are set. The PATH is reset to "/usr/local/bin:/bin:/usr/bin" or whatever was declared
in config.yaml. Any tools that
are consumed by a {checkout,build,package}Tools declaration are added to the
front of PATH. The same holds for ``$LD_LIBRARY_PATH`` with the difference of starting
completely empty.

Additionally, the following (environment) variables are populated
automatically:

* ``BOB_CWD``: Environment variable holding the working directory of the
  current script as absolute path.
* ``BOB_ALL_PATHS``: An associative array that holds the paths to the results
  of all dependencies indexed by the package name. This also includes indirect
  dependencies such as consumed tools or the sandbox.
* ``BOB_DEP_PATHS``: An associative array of all direct dependencies. This
  array comes in handy if you want to refer to a dependency by name (e.g.
  ``${BOB_DEP_PATHS[libfoo-dev]}``) instead of the position (e.g. ``$2``).
* ``BOB_TOOL_PATHS``: An associative array that holds the execution paths to
  consumed tools indexed by the package name. All these paths are in ``$PATH``
  resp. ``%PATH%``.

The associative arrays are no regular environment variables. Hence they are not
inherited by other processes that are invoked by the executed scripts. In bash
scripts they are associative arrays. See
`Bash Arrays <https://www.gnu.org/savannah-checkouts/gnu/bash/manual/bash.html#Arrays>`_
for more information. In PowerShell scripts they are defined as
`Hash Tables <https://docs.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_hash_tables>`_.

For PowerShell scripts a utility function called ``Check-Command`` is
available. It has two arguments: the first one (``ScriptBlock``) expects a
script block that is executed. The optional second argument (``ErrorAction``)
lets you override the error action. After the script block was executed the
``Check-Command`` function will check the last exit status and invoke the error
action if it is not zero. Example:

.. code-block:: powershell

    Check-Command { cmake --build . }

By default it will halt the script execution. This helper is needed because
there is no possibility to configure PowerShell to stop execution when an
external command fails. Make sure to wrap calls to external tools with
``Check-Command`` or check ``$lastexitcode`` yourself. Otherwise the build will
not detect errors involving external commands!

.. _configuration-recipes-setup:

{checkout,build,package}Setup[{Bash,Pwsh}]
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Type: String

Setup scripts are prepended to the executed regular scripts defined by
:ref:`configuration-recipes-scripts`. Defining a setup script does not yet
enable the corresponding step. Conceptually a setup script is intended to
define helper functions or variables but they should not yet execute anything.
They are included when entering the shell environment of a step (i.e. calling
``build.sh shell``). As such they are intended mainly for classes so that the
definitions of a class are automatically available in the shell environment.

A ``checkoutSetup`` script is always considered deterministic. That is, the
:ref:`configuration-recipes-checkoutdeterministic` setting only applies to
the ``checkoutScript``.

Other than the above differences setup scripts are identical to
:ref:`configuration-recipes-scripts`.

.. _configuration-recipes-tools:

{checkout,build,package}Tools
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Type: List of strings or tool dictionaries

This is a list of tools that should be added to ``$PATH`` during the execution
of the respective checkout/build/package script. A tool denotes a folder in an
(indirect) dependency. A tool might declare some library paths that are then
added to ``$LD_LIBRARY_PATH``.  The order of tools in ``$PATH`` and
``$LD_LIBRARY_PATH``  is unspecified.  It is assumed that each tool provides a
separate set of executables so that the order of their inclusion does not
matter.

In the simple form, a tool is only specified as simple string. This will use
the tool unconditionally::

    checkoutTools: [foo, bar]

If necessary, a tool can also be used conditionally. In this case the tool
is specified as a dictionary of the mandatory ``name`` and an optional ``if``
condition::

    checkoutTools:
        - name: foo
          if: "$CONDITION"
        - bar

The conditions will be checked with the final environment of a package, that is
after all dependencies of a recipe have been traversed.

A tool that is consumed in one step is also set in the following. This means a
tool consumed through checkoutTools is also available during the build and
package steps. Likewise a tool consumed by buildTools is available in the
package step too. The rationale is that all three steps form a small pipeline.
If a step depends on a certain tool then the result of the following step is
already indirectly dependent on this tool. Thus it can be available during the
following step anyway.

{checkout,build,package}ToolsWeak
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Type: List of strings

This property has the same semantics as :ref:`configuration-recipes-tools` with
one exception: despite the presence of the included tools the exact variants of
the tools are not considered to influence the result. That is, how the tools
are built and which versions of the tools are used, are ignored by Bob. They
neither contribute to variant management nor will they trigger a rebuild of a
package if they change.

Typical examples of weak tools are script interpreters like make or bash. The
exact version of these tools and the build flags of them are typically not
relevant for the build result of a package. These tools can be safely declared
as weak tools. On the other hand the C/C++-toolchain cannot be a weak tool
because the toolchain version and build time options (e.g. target architecture)
have a direct impact on the build results. Such toolchains must not be included
weakly.

.. _configuration-recipes-vars:

{checkout,build,package}Vars
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Type: List of strings

This is a list of environment variables that should be set during the execution
of the checkout/build/package script. This declares the dependency of the
respective step to the named variables.

It is not an error if a variable listed here is unset. This is especially
useful for classes or to implement default behaviour that can be overridden by
the user from the command line. If you expect a variable to be unset, it is your
responsibility to handle that case in the script. Every reference to such a
variable should be guarded with ``${VAR-somthing}`` or ``${VAR+something}``.

A variable that is consumed in one step is also set in the following. This
means a variable consumed through checkoutVars is also set during the build
and package steps. Likewise, a variable consumed by buildVars is set in the
package step too. The rationale is that all three steps form a small pipeline.
If a step depends on a certain variable then the result of the following step
is already indirectly dependent on this variable. Thus it can be set during the
following step anyway.

The following variables are populated internally by Bob and might be added to
the variable list:

* ``BOB_HOST_PLATFORM`` - the platform identifier where Bob is running on. The
  following values are defined:

  * ``linux``: Linux
  * ``msys``: Windows/MSYS2
  * ``cygwin``: Windows/Cygwin
  * ``win32``: Windows
  * ``darwin``: Mac OS X

* ``BOB_RECIPE_NAME`` - name of the recipe that defined the package
* ``BOB_PACKAGE_NAME`` - name of the actual package. Might be different from
  the recipe name if ``multiPackage`` is used.

Note that you should keep the usage of these variables to a minimum because
they may force separate builds of packages that are otherwise identical.  For
example using ``BOB_PACKAGE_NAME`` in ``buildVars`` will force separate builds
of all involved ``multiPackage`` keys even if they have a common
``buildScript`` because ``BOB_PACKAGE_NAME`` will be unique for each
``multiPackage`` entry.

.. _configuration-recipes-vars-weak:

{checkout,build,package}VarsWeak
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Type: List of strings

This is a list of environment variables that should be set during the execution
of the checkout/build/package script. These variables are not considered to
influence the result, very much like the variables listed in
:ref:`configuration-config-whitelist`.

.. warning::
   Bob expects that the content of these variables is irrelevant for the actual
   build result. They neither contribute to variant management nor will they
   trigger a rebuild of a package if they change.

For example, a typical usage of ``buildVarsWeak`` is to specify the number of
parallel make jobs. While it changes the behaviour of the job (the number of
parallel compiler processes) it will not change the actual build result. The
weak inclusion of a variable has no effect if it is also referenced by
:ref:`configuration-recipes-vars`. In this case the variable will always be
considered significant for the build result.

It is not an error that a variable listed here is unset. This is especially
useful for classes or to implement default behaviour that can be overridden by
the user from the command line. If you expect a variable to be unset it is your
responsibility to handle that case in the script. Every reference to such a
variable should be guarded with ``${VAR-somthing}`` or ``${VAR+something}``.

A variable that is consumed in one step is also set in the following. This
means a variable consumed through checkoutVarsWeak is also set during the build
and package steps. Likewise, a variable consumed by buildVarsWeak is set in the
package step too. The rationale is that all three steps form a small pipeline.
If a step depends on a certain variable then the result of the following step
is already indirectly dependent on this variable. Thus it can be set during the
following step anyway.

.. _configuration-recipes-netAccess:

{build,package}NetAccess
~~~~~~~~~~~~~~~~~~~~~~~~

Type: Boolean

By default the external network is not accessible during build or package steps
when building inside a sandbox. Checkout steps always have network access. If
such access is still needed a recipe may set the ``buildNetAccess`` or the
``packageNetAccess`` to ``True``.

.. warning::
   Bob assumes that build and package steps are deterministic. Do not rely on
   external state that changes the behavior of the build. Unless the input of a
   package changes (sources, dependencies) Bob will not re-build a package.

To configure the network access based on the actually used tools by a recipe
you can set the ``netAccess`` property in
:ref:`configuration-recipes-provideTools`. The ``{build,package}NetAccess``
should only be set if the script in the recipe itself requires the network
access during build or package steps.

.. _configuration-recipes-checkoutassert:

checkoutAssert
~~~~~~~~~~~~~~

Type: List of checkout assertions

Using ``checkoutAssert`` you can make a build fail if a file content has been
changed. This is especially useful to detect modifications in license files and
copyright notices in source files.

The following properties are supported:

+-----------------+------------------------------------------------------------------+
| Property        | Description                                                      |
+=================+==================================================================+
| ``file``        | The file in the workspace to check. Must be a relative path.     |
+-----------------+------------------------------------------------------------------+
| ``digestSHA1``  + Digest of the file / part (lower case). Either pre calculate it  |
|                 | using ``sha1sum`` command or take the output of the first        |
|                 | (failing) run.                                                   |
+-----------------+------------------------------------------------------------------+
| ``start``       | First line of the file that is checked. Optional integer number. |
|                 | Defaults to 1 (first line of file).                              |
+-----------------+------------------------------------------------------------------+
| ``end``         | Last line of file that is checked. Optional integer number.      |
|                 | Defaults to last line of file.                                   |
+-----------------+------------------------------------------------------------------+

Line numbers start at 1 and are inclusive. The ``start`` line is always taken
into account even if the ``end`` line is equal or smaller. The line terminator
is always ``\n`` (ASCII "LF", 0x0a) regardless of the host operating system.

String substitution is applied to every setting.

Example::

    checkoutAssert:
        - file: LICENSE
          digestSHA1: "2f7285314f4c057c75dbc0e5fad403b2d0691628"
        - file: src/namespace-sandbox/namespace-sandbox.c
          digestSHA1: "5ee22fb054c92560ec17202dec67202563e0d145"
          start: 3
          end: 13

.. _configuration-recipes-checkoutdeterministic:

checkoutDeterministic
~~~~~~~~~~~~~~~~~~~~~

Type: Boolean

By default a ``checkoutScript`` is considered indeterministic. The rationale is
that extra care must be taken for a script to fetch always the same sources. If
you are sure that the result of the checkout script is always the same you may
set this to ``True``.

The ``checkoutDeterministic`` keyword only relates to the ``checkoutScript`` at
the same level. Each recipe or class must declare the determinism of its
``checkoutScript``. If there is no ``checkoutScript`` then
``checkoutDeterministic`` implicitly defaults to ``True``. Everything in
``checkoutSCM`` is *not* affected by ``checkoutDeterministic``. All SCMs
included in Bob will determine their determinism based on the configuration
automatically, e.g. using a commit or tag is considered deterministic while
using a branch is indeterministic.

If the checkout is deemed deterministic it enables Bob to apply various
optimizations. Deterministic checkouts do not need to be executed every time
and binary artifacts can be searched without executing the checkout script at
all.

.. note::
    The ``checkoutDeterministic`` setting does not apply to the optional
    ``checkoutSetup`` script. Setup scripts are always considered
    deterministic.

.. _configuration-recipes-scm:

checkoutSCM
~~~~~~~~~~~

Type: SCM-Dictionary or List of SCM-Dictionaries

Bob understands several source code management systems natively. On one hand it
enables the usage of dedicated plugins on a Jenkins server. On the other hand
Bob can manage the checkout step workspace much better in the development build
mode.

All SCMs are fetched/updated before the checkoutScript of the package are run.
The checkoutScript should not move or modify the checkoutSCM directories,
though.

If the package consists of a single git module you can specify the SCM directly::

    checkoutSCM:
        scm: git
        url: git://git.kernel.org/pub/scm/network/ethtool/ethtool.git

If the package is built from multiple modules you can give a list of SCMs::

    checkoutSCM:
        -
            scm: git
            url: git://...
            dir: src/foo
        -
            scm: svn
            url: https://...
            dir: src/bar

There are three common (string) attributes in all SCM specifications: ``scm``,
``dir`` (\*) and ``if``. Additionally to the string type, the ``if`` property may
be given as IfExpression (see :ref:`configuration-principle-booleans`). By
default the SCMs check out to the root of the workspace. You may specify any
relative path in ``dir`` to checkout to this directory.

.. hint::
   The defaults of all attributes marked by an asterisk (\*) can be changed by
   :ref:`configuration-config-scmDefaults` in the user configuration.

Special care must be taken if SCMs are nested, that is the ``dir`` attribute of
one SCM is a subdirectory of another. Bob requires that the SCM with the upper
directory has to be in the list before the SCMs that are checked out into
subdirectories. Additionally, SCMs that are natively supported by Jenkins
plugins (git, svn), cannot be nested into the other SCMs (cvs, import, url).
The reason is that Jenkins SCM plugins always execute before anything else in a
Jenkins job.

By using ``if`` you can selectively enable or disable a particular SCM using
either a string or a expression. In case a string is given to the ``if``-keyword
it is substituted according to :ref:`configuration-principle-subst` and the final
string is interpreted as a boolean value (everything except the empty string, ``0``
and ``false`` is considered true). In case you're using the expression syntax you
can use :ref:`bobpaths_string_literals` and :ref:`bobpaths_string_function_calls`
to express a condition (see :ref:`configuration-principle-booleans`). The SCM
will only be considered if the condition passes.


Currently the following ``scm`` values are supported:

====== =======================================================================================
scm    Additional attributes
====== =======================================================================================
cvs    | ``cvsroot``: repository location ("``:ext:...``", path name, etc.)
       | ``module``: module name
       | ``rev``: revision, branch, or tag name (optional)
git    | ``url``: URL of remote repository
       | ``branch`` (\*): Branch to check out (optional, default: master)
       | ``tag``: Checkout this tag (optional, overrides branch attribute)
       | ``commit``: SHA1 commit Id to check out (optional, overrides branch or tag attribute)
       | ``rebase`` (\*): Rebase local branch instead of fast-forward merge update (optional, defaults to false)
       | ``rev``: Canonical git-rev-parse revision specification (optional, see below)
       | ``remote-*``: additional remote repositories (optional, see below)
       | ``sslVerify`` (\*): Whether to verify the SSL certificate when fetching (optional)
       | ``shallow`` (\*): Number of commits or cutoff date that should be fetched (optional)
       | ``singleBranch`` (\*): Fetch only single branch instead of all (optional)
       | ``submodules`` (\*): Whether to clone all / a subset of submodules. (optional)
       | ``recurseSubmodules`` (\*): Recusively clone submodules (optional, defaults to false)
       | ``shallowSubmodules`` (\*): Clone submodules shallowly (optional, defaults to true)
       | ``references`` (\*): Git reference. A local reference repo to be used as
       |       alternate (see man git-clone).
       |       A list of strings or a dictionaries with
       |        ``url``: (optional, Regex-String, default: ``.*``). The matching part
       |           of the remote URL is replaced by
       |        ``repo``: (String) local storage path.
       |        ``optional``: (Boolean, default True). Marks the reference as
       |           optional if true. Otherwise a error is raised if the
       |           local reference repo didn't exitst.
       |   Note: ``references`` are not used for submodules.
       | ``retries`` (\*): Number of retries before the checkout is set to failed.
       | ``dissociate``: (Boolean, default false). Dissociate the reference (see man git-clone).
import | ``url``: Directory path relative to project root.
       | ``prune`` (\*): Delete destination directory before importing files.
       | ``recipeRelative`` (\*): Whether ``url`` is relative to recipe or project root. (optional)
svn    | ``url``: URL of SVN module
       | ``revision``: Optional revision number (optional)
       | ``sslVerify`` (\*): Whether to verify the SSL certificate when fetching (optional)
url    | ``url``: File that should be downloaded
       | ``digestSHA1``: Expected SHA1 digest of the file (optional)
       | ``digestSHA256``: Expected SHA256 digest of the file (optional)
       | ``digestSHA512``: Expected SHA512 digest of the file (optional)
       | ``extract`` (\*): Extract directive (optional, default: auto)
       | ``fileName`` (\*): Local file name (optional, default: url file name)
       | ``fileMode`` (\*): File mode (optional, default depends on :ref:`policies-defaultFileMode` policy)
       | ``sslVerify`` (\*): Whether to verify the SSL certificate when fetching (optional)
       | ``stripComponents`` (\*): Number of leading components stripped from file name
       |                           (optional, tar files only)
       | ``retries`` (\*): Number of retries before the checkout is set to failed.
====== =======================================================================================

The following synthetic attributes exist. They are generated internally
and cannot be set in the recipe. They are intended to be matched in queries
or to show additional information.

* ``overridden``: Boolean that is true if a :ref:`configuration-config-scmOverrides`
  was applied. Otherwise false.
* ``recipe``: The file name of the recipe/class that defined this SCM

.. _Git: http://git-scm.com/
.. _Svn: http://subversion.apache.org/

Most SCMs support the ``sslVerify`` attribute. This is a boolean that controls
whether to verify the SSL certificate when fetching. If unset, it defaults to
``True``.  If at all possible, fixing a certificate problem is preferable to
using this option.

cvs
   The CVS SCM requires a ``cvsroot``, which is what you would normally put in
   your CVSROOT environment variable or pass to CVS using ``-d``. If you specify
   a revision, branch, or tag name, Bob will check out that instead of the HEAD.
   Unfortunately, because Bob cannot know beforehand whether the ``rev`` you gave
   it points to a branch or tag, it must consider this SCM nondeterministic.
   To check out using ssh, you can use the syntax ``:ssh:user@host:/path``,
   which will be translated into an appropriate ``CVS_RSH`` assignment by Bob.
   Alternatively, you can use a normal ``:ext:`` CVSROOT and manually pass the
   ``CVS_RSH`` value into the recipe using ``checkoutVars``.

git
   The ``git`` SCM requires at least an ``url`` attribute. The URL might be any
   valid Git URL. To checkout a branch other than *master* add a ``branch``
   attribute with the branch name. To checkout a tag instead of a branch specify
   it with ``tag``. You may specify the commit id directly with a ``commit``
   attribute too.

   .. note:: The default branch of the remote repository is not used. Bob will
      always checkout "master" unless ``branch``, ``tag`` or ``commit`` is given.

   If neiter a commit, nor a tag is specified, Bob will try to track the
   upstream branch with fast forward merges. This implies that updates will
   fail if the upstream repository has been rebased or there are local
   conflicting changes or commits. Set the ``rebase`` property to ``True`` to
   handle upstream rebases or local commits.

   .. attention:: Rebasing is a potentially dangerous operation. Make sure you
      read and understood the git rebase manpage before using this option.

   The ``rev`` property of the ``git`` SCM unifies the specification of the
   desired branch/tag/commit into one single property. If present it will be
   evaluated first. Any other ``branch``, ``tag`` or ``commit`` property is
   evaluated after it and may override a previous setting made by ``rev``. The
   branch/tag/commit precedence is still respected, though. Following the patterns
   described in git-rev-parse(1) the following formats are currently supported:

   * <sha1>, e.g. dae86e1950b1277e545cee180551750029cfe735.
     The full SHA-1 object name (40-byte hexadecimal string).
   * refs/tags/<tagname>, e.g. refs/tags/v1.0.
     The symbolic name of a tag.
   * refs/heads/<branchname>, e.g. refs/heads/master.
     The name of a branch.
   * refs/<refname>, e.g. refs/changes/50/19450/17. A generic ref that is
     neither a branch nor a tag. Effectively treated like a tag unless
     ``rebase`` is set to ``true`` in which case updates to the ref will rebase
     the workspace.

   The ``remote-*`` property allows adding extra remotes whereas the part after
   ``remote-`` corresponds to the remote name and the value given corresponds to
   the remote URL. For example ``remote-my_name`` set to ``some/url.git`` will
   result in an additional remote named ``my_name`` and the URL set to
   ``some/url.git``.

   To reduce the amount of data that is fetched from the remote repository the
   optional ``shallow`` attribute can be set. If it is an integer then only
   this number of commits are fetched from the tip of the remote branches
   (``--depth`` clone parameter). It can also be a string that should be a date
   understood by git (passed as ``--shallow-since=`` to git). Either option
   will imply ``singleBranch`` to be true. This further restricts the fetching
   of remote branches to the configured branch only. Set ``singleBranch``
   either to ``False`` to explicitly fetch all remote branches or to ``True``
   to fetch only the current branch, regardless of the ``shallow`` setting.

   .. tip:: You can set the ``shallow`` and ``singleBranch`` properties with
      :ref:`configuration-config-scmOverrides` too.  This can be used to
      improve the build times of existing projects or to fetch the whole
      history if ``shallow`` is used in the recipes.

   Another option is to use a local mirror of the repo. To use this define
   `references` either in the recipe or in :ref:`configuration-config-scmDefaults`.

   E.g. if the remote URL of your repo is 'git@foo.bar/repo.git' and you have a
   local mirror of this repo is at `/mirror/repo.git` put::

        references:
          -
           url: "git@foo.bar"
           repo: "/mirror/"
           optional: False

   in your default.yaml to use the local mirror. Note: The `url` parameter must
   match for every repo in the recipes. Use regex pattern to achive this.

   By default submodules will not be cloned. Set the ``submodules`` property to
   true to populate them automatically. You can also set it to a list of paths
   to clone only a subset of submodules. To recursively clone submodules of
   submodules too, set the ``recurseSubmodules`` property to ``True``::

      checkoutSCM:
          - scm: git
            url: foo@bar.test
            submodules: True           # clone all direct submodules
          - scm: git
            url: subset@foo.test
            submodules:                # clone only submodule "foo/bar"
               - foo/bar
          - scm: git
            url: something@else.test
            submodules: True           # clone submodules
            recurseSubmodules: True    # recursively for sub-submodules too

   The submodules will be cloned shallowly by default. To clone submodules with
   the whole history set ``shallowSubmodules`` to ``False``. Only submodules
   that are in detached HEAD state and are on the commit as recorded in the git
   tree will be automatically updated if the main module branch is updated.
   Otherwise a warning will be shown and the submodule won't be updated,
   including possible sub-submodules.

   .. attention:: Bob makes certain assumptions about your git usage. If any of
      the following conditions are violated you may run into undefined
      behaviour:

      * Tags never change. You must not replace a tag with different content.
      * The content of the git repository must not depend on the user
        authentication. See :ref:`policies-scmIgnoreUser` policy.
      * The build result is not influenced by shallow clones.

import
   The ``import`` SCM copies the directory specified in ``url`` to the
   workspace. By default, the destination is always overwritten and obsolete
   files are deleted. Set ``prune`` to ``False`` to only overwrite if the
   source file was changed more recently than the exiting destination in the
   workspace. Before Bob 0.18, the default was the other way around (see
   :ref:`policies-pruneImportScm`).

   In contrast to the other SCMs that fetch across the network, the ``import``
   SCM is always updated, even if ``--build-only`` is used. Because only local
   files are imported, there is no possibility to inadvertently fetch unwanted
   changes from other users. The files should thus always be edited at the
   import source location and not in the workspace.

   .. attention::
      Do not import large source trees when working with Jenkins builds. The
      content is included in the job configuration that will get too large
      otherwise.

   By default, the directory given in ``url`` is interpreted relative to the
   project root. Alternatively, ``url`` can be made relative to the recipe
   itself if ``recipeRelative`` is set to ``True``. This is recommended
   especially for recipes that are included as layers into other projects.

svn
   The `Svn`_ SCM, like git, requires the ``url`` attribute too. If you specify a
   numeric ``revision`` Bob considers the SCM as deterministic.

url
   The ``url`` SCM naturally needs an ``url`` attribute. This might be a proper
   URL (e.g. ``http://foo.bar/baz.tgz``) or a file name. The supported URL
   schemas depend on Pythons ``urllib`` module but ``http``, ``https``, ``ftp``
   and ``file`` should work. If a bare file name is specified, tilde expansion
   is performed. This replaces the initial ``~`` or ``~user`` by the *user*’s
   home directory.

   If a SHA digest is given with ``digestSHA1``, ``digestSHA256`` and/or
   ``digestSHA512``, the downloaded file will be checked for a matching hash
   sum. This also makes the URL deterministic for Bob. Otherwise, the URL will
   be checked in each build for updates.

   Based on the file name ending, Bob will try to extract the downloaded file.
   You may prevent this by setting the ``extract`` attribute to ``no`` or
   ``False``. If the heuristic fails, the extraction tool may be specified as
   ``tar``, ``gzip``, ``xz``, ``7z`` or ``zip`` directly. For ``tar`` files it
   is possible to strip a configurable number of leading components from file
   names on extraction by the ``stripComponents`` attribute.

   If the file is extracted, the original file will be kept next to the
   ``workspace`` to keep the directory clean. If the
   :ref:`policies-urlScmSeparateDownload` policy is on the old behavour, the
   downloaded file will still be in put the workspace, though.

   .. note::
       Starting with Bob 0.14 (see :ref:`policies-tidyUrlScm` policy) the whole
       directory where the file is downloaded is claimed by the SCM. It is not
       possible to fetch multiple files in the same directory. This is done to
       separate possibly extracted files safely from other checkouts.

   The file mode of the downloaded or copied file can be set with the
   ``fileMode`` attribute. Two formats are supported: bit masks (as
   octal number) or a symbolic string mode.  Both formats follow the ``chmod``
   command syntax. Examples: ``0764``, ``"u=rwx,g=rw,o=r"``. The ``fileMode``
   defaults to ``0600``/``"u=rw"`` unless the :ref:`policies-defaultFileMode`
   policy is configured for the old behaviour.

.. _configuration-recipes-checkoutUpdateIf:

checkoutUpdateIf
~~~~~~~~~~~~~~~~

Type: String | Boolean | ``null`` | IfExpression
(:ref:`configuration-principle-booleans`), default: ``False``

By default no checkout scripts are run when building with ``--build-only``.
Some use cases practically require the ``checkoutScript`` to be always run,
through. A typical example are code generators that generate sources from some
high level description. These generators must be run every time when the user
has changed the input. A recipe or class can explicitly opt in to run their
``checkoutScript`` also in build-only mode to cover such a use case. This is
done by either setting ``checkoutUpdateIf`` to ``True`` or by a boolean
expression that is evaluated to ``True``. Otherwise the ``checkoutScript`` is
ignored even if some other class enables its script. The ``checkoutUpdateIf``
property thus only applies to the corresponding ``checkoutScript`` in the same
recipe/class.

A ``null`` value has a special semantic. It does not enable the
``checkoutScript`` on ``--build-only`` builds by itself but only if some
inherited class or the recipe does enable its ``checkoutUpdateIf``. This is
useful for classes to provide some update functions but, unless an inheriting
recipe explicitly enables ``checkoutUpdateIf``, does not cause the checkout
step to run by itself in ``--build-only`` mode.

Examples::

    checkoutUpdateIf: False                             # default, same as if unset
    checkoutUpdateIf: True                              # unconditionally run checkoutScript
    checkoutUpdateIf: "$(is-tool-defined,idl-compiler)" # boolean expression
    checkoutUpdateIf: !expr |                           # IfExpression
                        is-tool-defined("idl-compiler")

.. _configuration-recipes-depends:

depends
~~~~~~~

Type: List of Strings or Dependency-Dictionaries

Declares a list of other recipes that this recipe depends on. Each list entry
might either be a single string with the recipe name or a dictionary with more
fine grained settings. Such entries might either name another recipe directly
(``name``) or a list of further dependencies (``depends``) that inherit the
settings from the current entry. See the following example for both formats::

    depends:
        - foo
        - bar
        - name: toolchain
          use: [tools, environment]
          forward: True
        - if: "${FOOBAR}"
          depends:
              - baz
              - qux

In the first and second case only the package is named, meaning the build
result of recipe *foo* resp. *bar* is fed as ``$2`` and ``$3`` to the build
script. Any provided dependencies of these packages
(:ref:`configuration-recipes-providedeps`) will be implicitly added to the
dependency list too.

In the third case a recipe named *toolchain* is required but instead of using
its result the recipe imports any declared tools and environment variables from
*toolchain*.  Additionally, because of the ``forward`` attribute, these
imported tools and variables are not only imported into the current recipe but
also forwarded to the following recipes (*baz* and *qux*).

The 4th case is a recursive definition where the simple dependencies *baz* and
*qux* are guarded by a common condition. These dependencies will only be
considered if the variable ``FOOBAR`` expands to a value that is evaluated as
boolean true. If the condition passes these dependencies will be available as
``$4`` and ``$5`` to the build script. Recursive definitions might be nested
freely and they might override any setting mentioned in the table below. All
``if`` properties on each nesting level must evaluate to true for an entry to
take effect.

Detailed entries must either contain a ``name`` property or a ``depends`` list.
The following settings are supported:

+-------------+-----------------+-----------------------------------------------------+
| Name        | Type            | Description                                         |
+=============+=================+=====================================================+
| name        | String          | The name of the required recipe.                    |
|             |                 | String substitution is applied to this setting.     |
+-------------+-----------------+-----------------------------------------------------+
| alias       | String          | Alias name of the dependency. Declares alternate    |
|             |                 | name for this dependency.                           |
+-------------+-----------------+-----------------------------------------------------+
| depends     | List of         | A list of dependencies inheriting the settings of   |
|             | Dependencies    | this entry.                                         |
+-------------+-----------------+-----------------------------------------------------+
| use         | List of strings | List of the results that are used from the package. |
|             |                 | The following values are allowed:                   |
|             |                 |                                                     |
|             |                 | * ``deps``: provided dependencies of the recipe.    |
|             |                 |   These dependencies will be added at the end of    |
|             |                 |   the dependency list unless the dependency is      |
|             |                 |   already on the list.                              |
|             |                 | * ``environment``: exported environment variables   |
|             |                 |   of the recipe.                                    |
|             |                 | * ``result``: build result of the recipe.           |
|             |                 | * ``tools``: declared build tools of the recipe.    |
|             |                 | * ``sandbox``:  declared sandbox of the recipe.     |
|             |                 |                                                     |
|             |                 | Default: Use the result and dependencies            |
|             |                 | (``[deps, result]``).                               |
+-------------+-----------------+-----------------------------------------------------+
| checkoutDep | Boolean         | If true, the dependency is available as argument to |
|             |                 | the checkout step. The build step will still have   |
|             |                 | access to this dependency.                          |
|             |                 |                                                     |
|             |                 | Defaults to false. Only relevant if ``result`` is   |
|             |                 | included in these ``use`` list.                     |
+-------------+-----------------+-----------------------------------------------------+
| forward     | Boolean         | If true, the imported environment, tools and        |
|             |                 | sandbox will be forwarded to the dependencies       |
|             |                 | following this one. Otherwise these variables,      |
|             |                 | tools and/or sandbox will only be accessible in the |
|             |                 | current recipe.                                     |
|             |                 |                                                     |
|             |                 | Default: False.                                     |
+-------------+-----------------+-----------------------------------------------------+
| environment | Dictionary      | This clause allows to define or override            |
|             | (String ->      | environment variables for the dependencies.         |
|             | String)         | Example::                                           |
|             |                 |                                                     |
|             |                 |    environment:                                     |
|             |                 |        FOO: value                                   |
|             |                 |        BAR: baz                                     |
|             |                 |        BAZ:                                         |
|             |                 |            value: "${VAR}"                          |
|             |                 |            if: "${CONDITION}"                       |
|             |                 |                                                     |
|             |                 | Value strings in this clause are subject to         |
|             |                 | :ref:`configuration-principle-subst`.               |
+-------------+-----------------+-----------------------------------------------------+
| if          | String |        | See :ref:`configuration-principle-booleans` for     |
|             | IfExpression    | evaluation details. The dependency is only          |
|             |                 | considered if the string/expression evaluates to    |
|             |                 | true. The follwing two examples are equivilent::    |
|             |                 |                                                     |
|             |                 |      if: "$(or,$(eq,$FOO,bar),$BAZ)"                |
|             |                 |                                                     |
|             |                 |      if: !expr |                                    |
|             |                 |            "${FOO}" == "bar" || "${BAZ}"            |
|             |                 |                                                     |
|             |                 | Default: "true"                                     |
+-------------+-----------------+-----------------------------------------------------+
| tools       | Dictionary      | Remap an existing tool to another name, possibly    |
|             | (String ->      | replacing the other tool. This is useful to change  |
|             | String)         | tools for a single dependency, e.g. using the host  |
|             |                 | toolchain for the dependency instead of the current |
|             |                 | cross compiling toolchain. Example::                |
|             |                 |                                                     |
|             |                 |     tools:                                          |
|             |                 |         target-toolchain: host-toolchain            |
|             |                 |                                                     |
|             |                 | This will replace ``target-toolchain`` for the      |
|             |                 | dependency with the current ``host-toolchain``.     |
|             |                 | At the dependency both names will refer to the same |
|             |                 | tool.                                               |
+-------------+-----------------+-----------------------------------------------------+
| inherit     | Boolean         | Inherit current environment, tools and sandbox to   |
|             |                 | this dependency. When set to ``false``, all         |
|             |                 | environment variables are reset to their default    |
|             |                 | and no tools or sandbox are passed down to the      |
|             |                 | dependency. This is mostly useful to make an        |
|             |                 | existing root-package become a dependency of        |
|             |                 | another (root) package.                             |
|             |                 |                                                     |
|             |                 | Default: ``true``                                   |
+-------------+-----------------+-----------------------------------------------------+

Each package in the dependency list must have a unique name. By default, the
name of the required recipe is used. This ensures that each dependency is named
only once. Also, provided dependencies from dependencies are merged based on
the package name (see :ref:`configuration-recipes-providedeps`).

Sometimes it is necessary to depend on the same recipe more than once because
multiple variants of the same recipe are required. In this case, alias names
can be used to give each dependency a unique name::

    depends:
        - name: some::package
          alias: some::package-alpha
          environment:
              VARIANT: alpha
        - name: some::package
          alias: some::package-beta
          environment:
              VARIANT: beta

This example assumes that the result of ``some::package`` depends on the
content of ``VARIANT``. The dependencies will be known as
``some::package-alpha`` and ``some::package-beta``, thus satisfying the unique
name requirement.  Both packages are based on the identical recipe
(``some::package``) but are built differently because of the varying
``VARIANT`` value.

.. _configuration-recipes-env:

environment
~~~~~~~~~~~

Type::

    {
        str : str | {
            "value" : str,
            Optional("if") : str | IfExpression
        }
    }

Defines environment variables in the scope of the current recipe. Any inherited
variables of the downstream recipe with the same name are overwritten. All
variables are passed to upstream recipes.

The definition of a variable can optionally be guarded by an ``if`` condition.
Only if the ``if`` property evaluates to true, the variable is actually
defined. Might be a string or an IfExpression. See
:ref:`configuration-principle-booleans` for details about the evaluation.

Examples::

    environment:
        PKG_VERSION: "1.2.3"

    environment:
        PKG_VERSION:
            value: "1.2.3"
            if: "$(eq,$FOO,bar)"

All environment keys are eligible to variable substitution. The environment of
the recipe and inherited classes are merged together. Suppose the project has
the following simple recipe/class structure::

    recipes/foo.yaml:
        inherit: [asan, werror]
        environment:
            CFLAGS: "${CFLAGS:-} -DFOO=1"

    classes/asan.yaml:
        environment:
            CFLAGS: "${CFLAGS:-} -fsanitize=address"

    classes/werror.yaml:
        environment:
            CFLAGS: "${CFLAGS:-} -Werror"

The definitions of the recipe has the highest precedence (i.e. it is
substituted last). Declarations of classes are substituted in their
inheritance order, that is, the last inherited class has the highest
precedence. Given the above example, the resulting ``CFLAGS`` would be
``${CFLAGS:-} -fsanitize=address -Werror -DFOO=1``

See also :ref:`configuration-recipes-privateenv`.

.. _configuration-recipes-filter:

filter
~~~~~~

Removed in version 0.25.


.. _configuration-recipes-fingerprintScript:

fingerprintScript[{Bash,Pwsh}]
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Type: String

The fingerprint script is executed before a package is built or downloaded.
The script is supposed to gather information about whatever external resource
is used in the recipe and output that in a stable format. The actual output is
irrelevant to Bob as long as it detects all relevant external influences of the
build result and that subsequent executions of the script generate the same
output if the external components have not changed.

.. note::
   Defining a ``fingerprintScript`` does not enable fingerprinting yet. At
   least one inherited class, used tool or the recipe itself must enable it by
   setting :ref:`configuration-recipes-fingerprintIf` accordingly.

Bob will incrementally rebuild the package whenever the fingerprint script
output changes. The output of the script is also used to tag binary artifacts.
An artifacts will only be downloaded if the fingerprint script generated the
same output. This enables Bob to prevent false sharing of binary artifacts
across otherwise incompatible machines.

The fingerprint script is executed in an empty temporary directory. It does not
have access to any dependencies of the recipe nor to the checked out sources.
A subset of environment variables of the package (see
:ref:`configuration-recipes-vars`) as defined by
:ref:`configuration-recipes-fingerprintVars` is set. The usual bash options are
applied (``nounset``, ``errexit``, ``pipefail``) too. If the script returns
with a non-zero exit status it will fail the build. The output on stderr is
ignored but will be displayed in the error message if the script fails. The
scripts of inherited classes are concatenated (but only if their
:ref:`configuration-recipes-fingerprintIf` condition did not evaluate to
``false``). Any fingerprint scripts that are defined by used tools (see
:ref:`configuration-recipes-provideTools`) are concatenated too.

The suffix of the keyword determines the language of the script. Using the
``Bash`` suffix (``fingerprintScriptBash``) defines a script that is
interpreted with ``bash``.  Likewise, the ``Pwsh`` suffix
(``fingerprintScriptPwsh``) defines a PowerShell script. Which language is used at
build time is determined by the :ref:`configuration-recipes-scriptLanguage` key
or, if nothing was specified, by the project
:ref:`configuration-config-scriptLanguage` setting. The keyword without a suffix
(``fingerprintScript``) is interpreted in whatever language is finally used at
build time. If both the keyword with the build time language suffix and without
a suffix are present then the keyword with the build language suffix takes
precedence.

For common fingerprint tasks the following built-in functions are provided by
Bob:

``bob-libc-version``
    Checks the host architecture together with the type and version of the libc
    library. The C-compiler that is used can be configured either with the
    first parameter of the function or it will use the ``CC`` environment
    variable. If both are not set the ``cc`` command is used.

    This helper should typically be used with the host compiler recipe.

``bob-libstdc++-version``
    Checks the host architecture together with the type and version of the C++
    standard library. The C++-compiler that is used can be configured either
    with the first parameter of the function or it will use the ``CXX``
    environment variable. If both are not set the ``c++`` command is used.

    This helper should typically be used with the host compiler recipe.

``bob-hash-libraries``
   Takes a list of libraries as arguments that should be hashed. This will link
   an executable that links with the given libraries, call ``ldd`` and hash all
   used libraries.

   Use this helper if no other information is available about a library /
   libraries except the name.

These helpers can be used in the fingerprint script. Their actual
implementation and output may change in the future as more systems are
supported by Bob.

.. _configuration-recipes-fingerprintIf:

fingerprintIf
~~~~~~~~~~~~~

Type: String | Boolean | ``null`` | IfExpression
(:ref:`configuration-principle-booleans`)

By default no fingerprinting is done unless at least one inherited class, used
tool or the recipe explicitly enables it. This is done by either setting
``fingerprintIf`` to ``True`` or by a boolean expression string that is
evaluated to ``True``. This can be used e.g. to apply a fingerprint only if the
package is built for the host and not cross-compiled. The
:ref:`configuration-recipes-fingerprintScript` of the recipe is only evaluated
if ``fingerprintIf`` is true. Otherwise the fingerprint script is ignored even
if some other class enables fingerprinting.  Setting ``fingerprintIf`` to
``False`` will unconditionally disable the associated ``fingerprintScript``.

A ``null`` value has a special semantic. It does not enable fingerprinting for
a package but retains the associated ``fingerprint`` script. If some
inherited class, the recipe or a used tool does enable fingerprinting then the
fingerprint script will still be evaluated. This is useful for classes to
provide some fingerprinting functions but, unless an inheriting recipe defines
a ``fingerprint`` script, does not enable fingerprinting of the recipe by
itself.

Examples::

   fingerprintIf: True                       # unconditionally enable fingerprinting
   fingerprintIf: "$(eq,${TOOLCHAIN},host)"  # boolean experession
   fingerprintIf: null                       # same as if unset
   fingerprintIf: !expr |                    # IfExpression
                     "${TOOLCHAIN}" == "host"

If not given it defaults to ``null``.

.. _configuration-recipes-fingerprintVars:

fingerprintVars
~~~~~~~~~~~~~~~

Type: List of strings

This declares the subset of the environment variables of the affected package
that should be set during the execution of the ``fingerprintScript``.  Only
variables that are selected by :ref:`configuration-recipes-vars` can be used.
It is not an error that a variable listed here is unset. The variables will
only be set if the corresponding ``fingerprintScript`` is enabled too.

inherit
~~~~~~~

Type: List of Strings

Include classes with the given name into the current recipe. Examples::

   inherit: [cmake]                          # inherit from cmake class
   inherit: [rootfs::cpio, rootfs:ext4]      # inherit from cpio and ext4 classes

Classes are searched in the ``classes/`` directory with the given name. The
syntax of classes is the same as the recipes. In particular classes can inherit
other classes too. The inheritance graph is traversed depth first and every
class is included exactly once.

All attributes of the class are merged with the attributes of the current
recipe. If the order is important the attributes of the class are put in front
of the respective attributes of the recipe. For example the scripts of the
inherited class of all steps are inserted in front of the scripts of the
current recipe.

.. _configuration-recipes-jobserver:

jobServer
~~~~~~~~~

Type: Boolean

Pass MAKEFLAGS Environment variable to the executed script with ``-j`` and
``--jobserver-auth`` set. This enables submakes or other tools to use Bobs
internal jobserver or even the jobserver of make calling bob. Bob also participating
and not starting any new step as long as no ticket is available.

Not available on Windows.

.. attention::
   The jobserver protocol does not specify if the pipe is blocking or
   non-blocking.  Bob uses non-blocking pipes like GNU make starting with
   version 4.3. Earlier versions of GNU make will fail with the following error
   message: ``*** read jobs pipe: Resource temporarily unavailable.  Stop.``.
   Either update your make version or disable the ``jobServer`` feature.

.. _configuration-recipes-metaenv:

metaEnvironment
~~~~~~~~~~~~~~~

Type::

    {
        str : str | {
            "value" : str,
            Optional("if") : str | IfExpression
        }
    }

metaEnvironment variables behave like :ref:`configuration-recipes-privateenv` variables.
They overrule other environment variables and can be used in all steps. In addition all
metaEnvironment variables are added to the audit no matter they are used in a step or not.
This predestines metaEnvironment variables to add the license type or version of a package.

The :ref:`manpage-query-meta` command can be used to retrieve metaEnvironment variables.

All metaEnvironment variables are subject to :ref:`string substitution
<configuration-principle-subst>`, unless the :ref:`policies-substituteMetaEnv`
policy is configured for the old behaviour.

The definition of a metaEnvironment variable can optionally be guarded by an
``if`` condition.  Only if the ``if`` property evaluates to true, the variable
is actually defined. Might be a string or an IfExpression. See
:ref:`configuration-principle-booleans` for details about the evaluation.

.. _configuration-recipes-multipackage:

multiPackage
~~~~~~~~~~~~

Type: Dictionary (String -> Recipe)

By utilizing the ``multiPackage`` keyword it is possible to unify multiple
recipes into one. The final package name is derived from the current recipe
name by appending the key under multiPackage separated by a "-".  If an empty
string is given as key the separator is not inserted. Nested multiPackages are
also supported. Every level of multiPackages appends another suffix to the
package name. The following example recipe foo.yaml declares four packages:
foo, foo-bar-x, foo-bar-y and foo-baz::

   multiPackage:
      "":
         ...
      bar:
         buildScript: ...
         multiPackage:
            x:
               packageScript: ...
            y:
               packageScript: ...
      baz:
         ...

All other keywords on the same level are treated as an anonymous base class that
is inherited by the defined multiPackage's. That way you can have common parts
to all multiPackage entries and keep just the distinct parts separately.

A typical use case for this feature are recipes for libraries. There are two
packages that are built from a library: a ``-target`` packet that has the
shared libraries needed during runtime and a ``-dev`` packet that has the
header files and other needed files to link with this library.

.. _configuration-recipes-privateenv:

privateEnvironment
~~~~~~~~~~~~~~~~~~

Type::

    {
        str : str | {
            "value" : str,
            Optional("if") : str | IfExpression
        }
    }

Defines environment variables just for the current recipe. Any inherited
variables with the same name of the downstream recipe or others that were
consumed from the dependencies are overwritten. All variables defined or
replaced by this keyword are private to the current recipe.

Example::

   privateEnvironment:
      APPLY_FOO_PATCH: "no"

The ``privateEnvironment`` of the recipe and inherited classes are merged
together.  See :ref:`configuration-recipes-env` for the merge and string
substitution behaviour.

The definition of a variable can optionally be guarded by an ``if`` condition.
Only if the ``if`` property evaluates to true, the variable is actually
defined. Might be a string or an IfExpression. See
:ref:`configuration-principle-booleans` for details about the evaluation.

.. _configuration-recipes-providedeps:

provideDeps
~~~~~~~~~~~

Type: List of Patterns

The ``provideDeps`` keyword receives a list of dependency names. These must be
dependencies of the current recipe, i.e. they must appear in the ``depends``
section. It is no error if the condition of such a dependency evaluates to
false. In this case the entry is silently dropped. To specify multiple
dependencies with a single entry shell globbing patterns may be used. As for the
names of the dependencies string substitution is also applied to ``provideDeps``.

Provided dependencies are subsequently injected into the dependency list of the
downstream recipe that has a dependency to this one (if ``deps`` is included in
the ``use`` attribute of the dependency, which is the default). This works in a
transitive fashion too, that is provided dependencies of an upstream recipe
are forwarded to the downstream recipe too.

Example::

   depends:
       - common-dev
       - communication-dev
       - config

   ...

   provideDeps: [ "*-dev" ]

Bob will make sure that the forwarded dependencies are compatible in the
injected recipe. That is, any duplicates through injected dependencies must
result in the same package being used.

.. _configuration-recipes-provideTools:

provideTools
~~~~~~~~~~~~

Type: Dictionary (String -> Path | Tool-Dictionary)

The ``provideTools`` keyword defines an arbitrary number of build tools that
may be used by other steps during the build process. In essence the definition
declares a path (and optionally several library paths) under a certain name
that, if consumed, are added to ``$PATH`` (and ``$LD_LIBRARY_PATH``) of
consuming recipes. Example::

   provideTools:
      host-toolchain:
         path: bin
         libs: [ "sysroot/lib/i386-linux-gnu", "sysroot/usr/lib", "sysroot/usr/lib/i386-linux-gnu" ]
         netAccess: True
         environment:
            CC: gcc
            CXX:
               value: g++
               if: "$ENABLE_CPP"
            LD: ld
         fingerprintIf: True
         fingerprintScript: |
            bob-libc-version gcc

The ``path`` attribute is always needed.  The ``libs`` attribute, if present,
must be a list of paths to needed shared libraries. Any path that is specified
must be relative. If the recipe makes use of existing host binaries and wants
to provide them as tool you should create symlinks to the host paths.

The ``netAccess`` attribute allows the tool to request network access during
build/package step execution even if the recipe has not requested it (see
:ref:`configuration-recipes-netAccess`). The network access is only granted if
the tool is used. This attribute might be needed if the recipe cannot know if a
particular tool actually requires network access. A prominent example are
proprietary compilers that need to talk to a license server. Unless a package
is built with such a compiler the network access is not needed.

The ``environment`` attribute provides the ability to define environment
variables that are automatically picked up by the recipe where the tool is
used. This allows for much more fine-grained variable provisioning than
:ref:`configuration-recipes-provideVars`. If multiple tools are used in a
recipe they must define distinct variables because no particular order between
tools is defined. The values defined in this attribute are subject to variable
substitution.

The definition of an ``environment`` variable can optionally be guarded by an
``if`` condition.  Only if the ``if`` property evaluates to true, the variable
is actually defined. Might be a string or an IfExpression. See
:ref:`configuration-principle-booleans` for details about the evaluation.

The ``fingerprintScript`` attribute defines a fingerprint script like in a
normal recipe by :ref:`configuration-recipes-fingerprintScript`. A fingerprint
script defined by a tool is implicitly added to the fingerprint scripts of all
recipes that use the particular tool. Use it to automatically apply a
fingerprint to all recipes whose result will depend on the host environment by
using the tool.  The ``fingerprintIf`` and ``fingerprintVars`` attributes are
handled the in the same way.

If no attributes except ``path`` are present the declaration may be abbreviated
by giving the relative path directly::

   provideTools:
      host-toolchain: bin

.. _configuration-recipes-provideVars:

provideVars
~~~~~~~~~~~

Type::

    {
        str : str | {
            "value" : str,
            Optional("if") : str | IfExpression
        }
    }

Declares arbitrary environment variables with values that should be passed to
the downstream recipe. The values of the declared variables are subject to
variable substitution. The substituted values are taken from the current
package environment. Example::

    provideVars:
        ARCH: "arm"
        CROSS_COMPILE: "arm-linux-${ABI}-"


The definition of a variable can optionally be guarded by an ``if`` condition.
Only if the ``if`` property evaluates to true, the variable is actually
defined. Might be a string or an IfExpression. See
:ref:`configuration-principle-booleans` for details about the evaluation.

By default these provided variables are not picked up by downstream recipes. This
must be declared explicitly by a ``use: [environment]`` attribute in the
dependency section of the downstream recipe. Only then are the provided variables
merged into the downstream recipes environment.

.. _configuration-recipes-provideSandbox:

provideSandbox
~~~~~~~~~~~~~~

Type: Sandbox-Dictionary

The ``provideSandbox`` keyword offers the current recipe as sandbox for the
downstream recipe. Any consuming downstream recipe (via ``use: [sandbox]``) will
be built in a sandbox where the root file system is the result of the current
recipe. The initial ``$PATH`` is defined with the required ``paths`` keyword
that should hold a list of paths. This will completely replace ``$PATH`` of
the host for consuming recipes.

.. attention::
    The build result is considered to be an invariant of such a sandbox. This
    implies that recipes shall produce the same result, regardless whether the
    sandbox is used or not.

Optionally there can be a ``mount`` keyword. With ``mount`` it is possible to
specify additional paths of the host that are mounted read only in the sandbox.
The paths are specified as a list of either strings or lists of two or three
elements. Use a simple string when host and sandbox path are the same without
any special options. To specify distinct paths use a list with two entries
where the host path is the first element and the second element is the path in
the sandbox.

The long format with three items additionally allows to specify a list of mount
flags. The shorter formats described above have no flags set. The following
flags are available:

* ``nofail``: Don't fail the build if the host path is not available. Instead
  drop the mount silently.
* ``nolocal``: Do not use this mount in local builds.
* ``nojenkins``: Do not use this mount in Jenkins builds.
* ``rw``: Mount as read-writable instead of read-only.

Additionally there can be an optional ``environment`` keyword. This works like
the :ref:`configuration-recipes-provideVars` keyword and defines environment
variables that are picked up by the depending recipe. In contrast to
``provideVars`` the variables defined here are only consumed if the sandbox is
actually used (i.e. the parent recipe defined ``sandbox`` in the ``use``
section and the user builds with ``--sandbox``). In this case the variables
defined here have a higher precedence that the ones defined in ``provideVars``.

The definition of an ``environment`` variable can optionally be guarded by an
``if`` condition.  Only if the ``if`` property evaluates to true, the variable
is actually defined. Might be a string or an IfExpression. See
:ref:`configuration-principle-booleans` for details about the evaluation.

Variable substitution is possible for the mount paths and environment
variables. See :ref:`configuration-principle-subst` for the available
substations. The mount paths are also subject to an additional variable
expansion when a step using the sandbox *is actually executed*. This can be
useful e.g. to expand variables that are only available on the build server.

By default, the user ID inside the sandbox is ``nobody``. The optional ``user``
key allows to use two other identities: ``root`` or ``$USER``.  Note that using
``root`` does not provide any more privileges. It merely maps the current user
ID to the root user ID inside the sandbox. The ``$USER`` option keeps the
current user ID when entering the sandbox. No other values are allowed.

Example::

    provideSandbox:
        paths: ["/bin", "/usr/bin"]
        mount:
            - "/etc/resolv.conf"
            - "${MYREPO}"
            - "\\$HOME/.ssh"
            - ["\\$SSH_AUTH_SOCK", "\\SSH_AUTH_SOCK", [nofail, nojenkins]]
        environment:
            AUTOCONF_BUILD: "x86_64-linux-gnu"
            ORIGINAL_ARCH:
                value: "$AUTOCONF_BUILD"
                if: "$(ne,$AUTOCONF_BUILD,x86_64-linux-gnu)"
        user: nobody

The example assumes that the variable ``MYREPO`` was set somewhere in the
recipes. On the other hand ``$HOME`` is expanded later at build time. This is
quite useful on Jenkins because the home directory there is certainly
different from the one where Bob runs. The last entry shows two mount option
being used. This line mounts the ssh-agent socket into the sandbox if
available. This won't be done on Jenkins at all and the build will proceed even
if ``$SSH_AUTH_SOCK`` is unset or invalid. Note that such variables have to be
in the :ref:`configuration-config-whitelist` to be available to the shell.

.. note::
    The mount paths are considered invariants of the build. That is changing the
    mounts will neither automatically cause a rebuild of the sandbox (and affected
    packages) nor will binary artifacts be re-fetched.

The user might amend the mount and search paths in ``default.yaml`` by a
:ref:`configuration-config-sandbox` entry. The user identity can be overridden
too.

.. _configuration-recipes-relocatable:

relocatable
~~~~~~~~~~~

Type: Boolean

If ``True``, Bob can assume that the package result is independent of the actual
location in the file system. Usually, all packages should be relocatable as this
is a fundamental assumption of Bob's working model. There might be particular
tools, though, that depend on their installed location. For such tools the
property should be set to ``False``.

If the property is not set, the default will be ``True``.  Inherited values
from a class will be overwritten by the recipe or inheriting class.

.. _configuration-recipes-root:

root
~~~~

Type: Boolean | String | IfExpression (:ref:`configuration-principle-booleans`)

Recipe attribute which defaults to ``False``. If set to ``True`` the recipe is
declared a root recipe and becomes a top level package. If a string
IfExpression is given it is subject to variable expansion and is interpreted as
boolean according to the rules explained in
:ref:`configuration-principle-booleans`.

.. _configuration-recipes-scriptLanguage:

scriptLanguage
~~~~~~~~~~~~~~

Type: Enumeration: ``bash``, ``PowerShell``.

Defines the scripting language which is used to run the
``{checkout,build,package,fingerprint}Script`` scripts when building the
package. If nothing is specified the :ref:`configuration-config-scriptLanguage`
setting from config.yaml is used. Depending on the chosen language Bob will
either invoke ``bash`` or ``pwsh``/``powershell`` as script interpreter. In
either case the command must be present in ``$PATH``/``%PATH%``.

.. _configuration-recipes-shared:

shared
~~~~~~

Type: Boolean

Marking a recipe as shared implies that the result may be shared between
different projects or workspaces. Only completely deterministic packages may be
marked as such. Typically large static packages (such as toolchains) are
enabled as shared packages. By reusing the result the hard disk usage can be
sometimes reduced drastically.

The exact behaviour depends on the build backend. For local builds the location
is configured by :ref:`configuration-config-share` in ``default.yaml``. On
Jenkins the result will be copied to a separate directory in the Jenkins
installation and will be used from there. This reduces the job workspace size
considerably at the expense of having artifacts outside of Jenkins's regular
control.

.. _configuration-aliases:

Alias definitions
-----------------

An alias package can be defined by placing an appropriately named YAML file in
the ``aliases`` directory. The naming convention is identical to the
``recipes`` directory. That is, the alias name is derived from the file name
after removing the ``.yaml`` suffix. Subdirectories can be used and the path
separator is replaced by ``::``.

Examples:

.. code-block:: none

    aliases/foo.yaml
    aliases/bar/baz.yaml

This will create two alias packages: ``foo`` and ``bar::baz``. Each YAML file
contains either a single string, e.g.::

    "target::package"

or defines multiple aliases with the help of a ``multiPackage`` dictionary::

    multiPackage:
        "":  ${CONFIG_SELECT_LIBEGL:-libs::mesa3d}
        dev: ${CONFIG_SELECT_LIBEGL:-libs::mesa3d}-dev
        tgt: ${CONFIG_SELECT_LIBEGL:-libs::mesa3d}-tgt

In contrast to recipe or class :ref:`configuration-recipes-multipackage`
definitions, aliases do not support nesting of ``multiPackage``. Like recipes,
the key in the ``multiPackage`` is appended to the base package name. Suppose
the above YAML file is stored as ``aliases/libs/libegl.yaml``, this will define
the ``libs::libegl``, ``libs::libegl-dev`` and ``libs::libegl-tgt`` aliases. If
``CONFIG_SELECT_LIBEGL`` is unset, the alias targets will be provided by
``libs::mesa3d``. Otherwise the variable will hold the alias target package
name.

The alias string is subject to :ref:`configuration-principle-subst`. This
enables aliases to select dynamically between different targets based on the
position in the dependency graph.

.. _configuration-config:

Project configuration (config.yaml)
-----------------------------------

The file ``config.yaml`` holds all static configuration options that are not
subject to be changed when building packages. The following sections describe
the top level keys that are currently understood. The file is optional or could
be empty.

.. _configuration-bobMinimumVersion:

bobMinimumVersion
~~~~~~~~~~~~~~~~~

Type: String

Defines the minimum required version of Bob that is needed to build this
project. Any older version will refuse to build the project. The version number
given here might be any prefix of the actual version number, e.g. "0.1" instead
of the actual version number (e.g. "0.1.42"). Bob's version number is specified
according to `Semantic Versioning`_. Therefore it is usually only needed to
specify the major and minor version.

The version string has to be compliant to Python `PEP 440`_. It is allowed to
specify pre-release versions (e.g. ``0.16.0rc1``) and even development versions
(e.g. ``0.15.1.dev42``). A version without pre-release suffix is considered
more recent than a version with a pre-release suffix. The development release
number is only relevant if the main version and the pre-release versions are
equal.

.. _Semantic Versioning: http://semver.org/
.. _PEP 440: https://www.python.org/dev/peps/pep-0440/

.. _configuration-config-layers:

layers
~~~~~~

Type: List of strings or SCM-Dictionaries

The ``layers`` section consists of a list of layer names that are then expected
in the ``layers`` directory of the project root directory::

    layers:
        - myapp
        - bsp

Layers that are not named in this section but that are present in the
``layers`` directory are ignored. Layers that are named but that do not exist
lead to a parse error. Layers can be nested, that is, a layer can itself have
layers below it.

The order of layers is important with respect to settings made by
``default.yaml`` in the various layers. The project root has the highest
precedence. The layers in ``config.yaml`` are named from highest to lowest
precedence. A layer with a higher precedence can override settings from layers
of lower precedence.

See :ref:`configuration` for more information.

Typically, layers are stored in their own SCM. To provide them to the root
recipes, common SCM-methods like git-submodules can be used. Another
possibility is to provide an SCM-Dictionary (see
:ref:`configuration-recipes-scm`) and let Bob manage the layer::

    layers:
        - name: myapp
          scm: git
          url: git@foo.bar:myapp.git
          commit: ...
        - bsp

.. note::
   Managed layers are only supported if the :ref:`policies-managedLayers`
   policy is set to the new behaviour.

If a layer SCM specification is given, Bob takes care of the layer management:

- Layers are checked out / updated automatically during
  :ref:`manpage-dev`/:ref:`manpage-build` (except ``--build-only`` builds).
- The ``bob layers`` command can update layers or show their status (see
  :ref:`manpage-layers`).

.. note::
   SCM backed layers are checked out into the build tree rather than the
   project root directory. This is important as soon as an out-of-source
   build tree is used (see :ref:`manpage-bob-init`).

Only git, svn, url and cvs SCMs are supported for layers. Because layers are
fetched and updated before any :ref:`configuration-config-usr` is parsed, the
regular ``whitelist`` and ``scmOverrides`` settings are not used. Instead,
layer checkouts are controlled by ``layersWhitelist`` and
``layersScmOverrides``. These settings can be optionally overridden from the
command line by passing a layers configuration file with the ``-lc`` option.

layersWhitelist
~~~~~~~~~~~~~~~

Whitelist for layers update only. See :ref:`configuration-config-whitelist`.

.. _configuration-config-layersScmOverrides:

layersScmOverrides
~~~~~~~~~~~~~~~~~~

:ref:`configuration-config-scmOverrides` used by layers checkout / update.
Conditional overrides are not supported.

.. _configuration-config-plugins:

plugins
~~~~~~~

Type: List of strings

Plugins are loaded in the same order as listed here. For each name in this
section there must be a .py-file in the ``plugins`` directory next to the
recipes. For a detailed description of plugins see :ref:`extending-plugins`.

.. _configuration-config-policies:

policies
~~~~~~~~

Type: Dictionaly (Policy name -> Bool)

The policies section allows to individually set policies to their old
(disabled) or new (enabled) behaviour. See :ref:`policies-defined` for a list
of all policies and their rationale.

Example::

    policies:
        defaultFileMode: False

This will explicitly request old behaviour for the ``defaultFileMode`` policy.

.. _configuration-config-scriptLanguage:

scriptLanguage
~~~~~~~~~~~~~~

Type: Enumeration: ``bash``, ``PowerShell``.

Defines the scripting language which is used to run the
``{checkout,build,package,fingerprint}Script`` scripts. Defaults to ``bash``.
Might be overrided on a case-by-case basis in a class or recipe with
:ref:`configuration-recipes-scriptLanguage`.  Depending on the chosen language
Bob will either invoke ``bash`` or ``pwsh``/``powershell`` as script
interpreter. In either case the command must be present in
``$PATH``/``%PATH%``.

.. important::
   Each layer configures the default language individually. That is, layers
   with higher precedence will not override the setting of layers with lower
   precedence.

.. _configuration-config-usr:

User configuration (default.yaml)
---------------------------------

The ``default.yaml`` file holds configuration options that may be overridden by
the user. Most commands will also take an '-c' option where any number of
additional configuration files with the same syntax can be specified.

Like git there are three locations where bob is looking for a
configuration file. They are parsed in descending order making it
possible to locally override global settings.::

    /etc/bobdefault.yaml:
        System-wide configuration file.

    $XDG_CONFIG_HOME/bob/default.yaml resp. ~/.config/bob/default.yaml:
        User-specific configuration File. If XDG_CONFIG_HOME is not set
        ~/.config/bob/default.yaml is used.

    ./default.yaml.
        Workspace-specific configuration file.

User configuration files may optionally include other configuration files.
Files are included relative to the currently processed file.
These includes are parsed *after* the current file, meaning that options of
included configuration files take precedence over the current one. Included
files do not need to exist and are silently ignored if missing. Includes are
specified without the .yaml extension::

    include:
        - overrides

It is possible for plugins to define additional settings. See
:ref:`extending-settings` for more information. Their meaning and typing is
completely controlled by the respective plugin and Bob will just pass the data
as-is without further interpretation.

User configuration files may also require specific files to be included. The
``require`` keyword behaves just like the ``include`` keyword with the
exception that Bob raises a parsing error if the file to be included cannot be
found::

     require:
        - overrides
        - /path/to/some/file

Required include files have a lower precedence that optional include files.

alias
~~~~~

Type: Dictionary (String -> String)

Aliases allow a string to be substituted for the first step of a
:ref:`relative location path <manpage-bobpaths-locationpath>`::

   alias:
      myApp: "host/files/group/app42"
      allTests: "//*-unittest"

See :ref:`manpage-bobpaths-aliases` for the rules that apply to aliases.

.. _configuration-config-archive:

archive
~~~~~~~

Type: Dictionary or list of dictionaries

The ``archive`` key configures the default binary artifact server(s) that
should be used. It is either directly an archive backend entry or a list of
archive backends. For each entry at least the ``backend`` key must be specified.
Optionally there can be a ``name`` key that is used in log output, a ``retries`` key
that determines the amount of retries for failed operations (default is 1,
only used for `http` backend) and a ``flags`` key that receives a list of various
flags, in particular for what operations the backend might be used. See the
following list for possible flags.
The default is ``[download, upload]``.

``download``
    Use this archive to download artifacts. Note that you still have to
    explicitly enable downloads on Jenkins servers. For local builds the exact
    download behaviour depends on the build mode (release vs. develop).

``upload``
    Use this archive to upload artifacts. To actually upload to the archive the
    build must be performed with uploads enabled (``--upload``).
``managed``
    This archive is managed, meaning the files can be iterated and deleted.
    This is required for the archive command to work.
``cache``
    Use this archive to cache downloaded artifacts from other archives. If a
    binary artifact was successfully downloaded from another archive it will
    be uploaded into this archive, unless it already exists there. Useful to
    cache artifacts locally on slow network connections.

``nofail``
    Don't fail the build if the upload or download from this archive fails. In
    any case it is never an error if a download does not find the requested
    archive on the backend. This option additionally suppresses other errors
    such as unknown hosts or interrupted transfers.

``nolocal``
    Do not use this archive in local builds.

``nojenkins``
    Do not use this archive in Jenkins builds.

Depending on the backend further specific keys are available or required. See
the following table for supported backends and their configuration.

=========== ===================================================================
Backend     Description
=========== ===================================================================
none        Do not use a binary repository (default).
azure       Microsoft Azure Blob storage backend. The account must be specified
            in the ``account`` key. Either a ``key`` or a ``sasToken`` may
            be set to authenticate, otherwise an anonymous access is used.
            Finally the container must be given in ``container``. Requires the
            ``azure-storage-blob`` Python3 library to be installed.
file        Use a local directory as binary artifact repository. The directory
            should be specified in the ``path`` key as absolute path. An
            initial ``~`` or ``~user`` component is replaced by the users home
            directory. The optional ``fileMode`` and ``directoryMode`` keys
            take the desired access modes as numeric value to override the
            default umask derived modes.
http        Uses a HTTP server as binary artifact repository. The server has to
            support the HEAD, PUT and GET methods. The base URL is given in the
            ``url`` key. The optional ``sslVerify`` boolean key controls
            whether to verify the SSL certificate.
shell       This backend can be used to execute commands that do the actual up-
            or download. A ``download`` and/or ``upload`` key provides the
            commands that are executed for the respective operation. The
            configured commands are executed by bash and are expected to copy
            between the local archive (given as ``$BOB_LOCAL_ARTIFACT``) and
            the remote one (available as ``$BOB_REMOTE_ARTIFACT``). See the
            example below for a possible use with ``scp``.
=========== ===================================================================

The directory layouts of the ``azure``, ``file``, ``http`` and ``shell``
(``$BOB_REMOTE_ARTIFACT``) backends are compatible. If multiple download
backends are available they will be tried in order until a matching artifact is
found. All available upload backends are used for uploading artifacts. Any
failing upload will fail the whole build.

.. note::
   The uploaded artifacts can be managed by :ref:`manpage-archive`. It might be
   wise to use different repositories for release builds and for continous
   builds to keep them separated.

Example::

   archive:
      backend: http
      url: "http://localhost:8001/upload"
      retries: 2
      name: "http-backend"

HTTP basic authentication is supported. The user name and password must be put
in the URL. Be careful to escape special characters of the password with proper
percent encoding::

   archive:
      backend: http
      url: "https://user:passw%40rd@server.test/artifacts"

.. warning::
   The password will be part of the Jenkins job configuration. Anybody who can
   read the jobs ``config.xml`` will be able to retrieve the password!

It is also possible to use separate methods for upload and download::

    archive:
        -
            backend: http
            name: "http-archive"
            url: "http://localhost:8001/archive"
            flags: [download]
        -
            backend: shell
            name: "ssh-archive"
            upload: "scp -q ${BOB_LOCAL_ARTIFACT} localhost:archive/${BOB_REMOTE_ARTIFACT}"
            download: "scp -q localhost:archive/${BOB_REMOTE_ARTIFACT} ${BOB_LOCAL_ARTIFACT}"
            flags: [upload]

The azure backend can also be used in conjunction with the http backend in case
of publicly readable containers. Given a typical configuration like this::

    archive:
        backend: azure
        account: <account>
        container: <container name>
        key: <access key>

the anonymous access to the container can be used like this::

    archive:
        backend: http
        url: https://<account>.blob.core.windows.net/<container name>
        flags: [download]

The ``flags: [download]`` makes sure that Bob does not try to upload artifacts
in case other backends are configured too.

.. _configuration-config-archive-prepend-append:

archive{Prepend,Append}
~~~~~~~~~~~~~~~~~~~~~~~

Type: Dictionary or list of dictionaries

These keys receive the same archive specification(s) like the :ref:`configuration-config-archive`
keyword. Compared to the ``archive`` key, which replaces the currently configured
archives, the ``archivePrepend`` key prepends the given archive(s) to the current list and
``archiveAppend`` appends to it. See :ref:`configuration-config-archive` for more details.

It is usually advisable to use these keywords instead of ``archive`` to enable
interoperability between projects, layers and the local user configuration.

.. _configuration-config-commands:

command
~~~~~~~

Type: Dict of command dicts

Override default command settings::

    command:
        dev:
            [..]
        build:
            [..]
        graph:
            [..]

build / dev
^^^^^^^^^^^

Set default build arguments here. See :ref:`manpage-dev` or
:ref:`manpage-build` for details.::

    command:
        dev:
            no_logfile: True
            build_mode: "build-only"
        build:
            verbosity: 3
            download: No

The following table lists possible arguments and their type:

=============== ====================== ===============================================
Key             Command line switch    Type
=============== ====================== ===============================================
always_checkout ``--always-checkout``  List of strings (regular expression patterns)
attic           ``--[no]-attic``       Boolean
audit           ``--[no]-audit``       Boolean
build_mode      ``-b`` | ``-B`` |      String (``normal``, ``build-only`` or
                ``--normal``           ``checkout-only``)
clean           ``--clean`` |          Boolean
                ``--incremental``
clean_checkout  ``--clean-checkout``   Boolean
destination     ``--destination``      String (Path)
download        ``--download``         String (``yes``, ``no``, ``deps``, ``forced``,
                                       ``forced-deps``, ``forced-fallback`` or
                                       ``packages=<packages>``)
download_layer  ``--download-layer``   List of strings (``yes=<layer>``,
                                       ``no=<layer>``, ``forced=<layer>```)
force           ``-f``                 Boolean
install         ``--[no-]install``     Boolean
jobs            ``-j``                 Integer
link_deps       ``--[no-]link-deps``   Boolean
no_deps         ``-n``                 Boolean
no_logfiles     ``--no-logfiles``      Boolean
sandbox         ``--[no-]sandbox`` |   Boolean / String (``yes``, ``no``, ``slim``,
                ``--slim-sandbox`` |   ``dev``, ``strict``)
                ``--dev-sandbox`` |
                ``--strict-sandbox``
shared          ``--[no-]shared``      Boolean
upload          ``--upload``           Boolean
verbosity       ``-q | -v``            Integer (-2[quiet] .. 3[verbose], default 0)
=============== ====================== ===============================================

graph
^^^^^

Set default graph arguments here. See :ref:`manpage-graph` for details.::

    command:
        graph:
            options:
                d3.dragNodes: True
            type: "d3"
            max_depth: 2

Supported arguments and their type:

=============== ===================================================================
Key             Type
=============== ===================================================================
options         Dictonary of String key value pairs
type            "d3" or "dot"
max_depth       Integer
=============== ===================================================================

.. _configuration-config-environment:

environment
~~~~~~~~~~~

Type: Dictionary (String -> String)

Specifies default environment variables. Example::

   environment:
      # Number of make jobs is determined by the number of available processors
      # (nproc).  If desired it can be set to a specific number, e.g. "2". See
      # classes/make.yaml for details.
      MAKE_JOBS: "nproc"

These variables are subject to :ref:`configuration-principle-subst` with the
current OS environment. This allows to take over certain variables from the OS
environment in a controlled fashion.

.. _configuration-config-hooks:

hooks
~~~~~

Hooks are other programs or scripts that can be executed by Bob at certain
points, e.g. before or after a build. Unless otherwise noted they are executed
with the project root directory as working directory. Example::

    hooks:
        postBuildHook: ./contrib/notify.sh

where ``contrib/notify.sh`` is:

.. code-block:: bash

    #!/bin/bash
    HEADLINE="Bob build finished"
    BODY="The build in $PWD has finished: $1"
    if [[ ${XDG_CURRENT_DESKTOP:-unknown} == KDE ]] ; then
        kdialog --passivepopup "$BODY" 10 --title "$HEADLINE"
    else
        notify-send -u normal -t 10000 "$HEADLINE" "$BODY"
    fi

The currently supported hooks are described below.

preBuildHook
    The pre-build hook is run directly before a local build (bob dev / bob
    build). It receives the paths of all packages that are built as arguments.

    If the hook returns with a non-zero status the build will be interrupted.

postBuildHook
    The post-build hook is run after a local build finished, regardless if the
    build succeeded or failed. It receives the status as first argument
    (``success`` or ``fail``) and the relative paths to the workspaces of the
    results as further arguments.

    The return status of the hook is ignored.

.. _configuration-config-mirrors:

{pre,fallback}Mirror[{Prepend,Append}]
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Type: Mirror-entry or list of mirror-entries

Define alternate URLs that are checked either before (``preMirror``) or as a
fallback (``fallbackMirror``) to the primary URL as defined in the SCM.
Optionally, these mirrors can be populated during the build process. This is
primarily useful for local ``preMirror``'s so that the files are available on
the next build and the original server does not need to be used any more,
saving build time and bandwidth on the upstream server.

Mirrors are only used for fully deterministic SCMs. The reason is that
otherwise the URL would not be interchangeable because every server could
provide a different result. For the same reason it is not possible to mirror
between different methods e.g., use a HTTP URL SCM mirror for a git SCM. It is
not possible to independently verify the equivalence of the mirror in such a
case.

When used without suffix (i.e. ``preMirror`` or ``fallbackMirror``), the
currently configured list of mirrors is replaced. By using the ``Prepend``
suffix, e.g. ``preMirrorPrepend``, the given mirror(s) is/are prepended to the
configured list of mirrors. Likewise, the ``Append`` suffix (e.g.
``fallbackMirrorAppend``) appends to the list of mirrors. It is advised to use
these suffixes instead of the bare ``preMirror``/``fallbackMirror`` to enable
interoperability between projects, layers and the local user configuration.

Each mirror entry specifies the SCM type (``scm``), a regular expression to
match the URL (``url``) and a replacement URL (``mirror``). Optionally, it is
possible to upload to the mirror (``upload``). Currently only the URL SCM is
supported for mirrors.

Examples::

    fallbackMirror:
        - scm: url
          url: "https?://ftp.gnu.org/(pub/)?gnu/(.*)"
          mirror: "http://mirror.netcologne.de/gnu/\\2"
        - scm: url
          url: "https?://ftp.gnu.org/(pub/)?gnu/(.*)"
          mirror: "http://www.mirrorservice.org/sites/ftp.gnu.org/gnu/\\2"

A typical mirror configuration for the global user configuration could look
like the following. It mirrors all remote URLs to a local directory::

    preMirrorPrepend:
        scm: url
        url: "https?://.*/(.*)"
        mirror: "~/.cache/bob/mirror/\\1"
        upload: True

This will put all downloaded files into a caching directory of the current
user.

.. _configuration-config-rootFilter:

rootFilter
~~~~~~~~~~

Type: List of Strings

Filter root recipes. The effect of this is a faster package parsing due to the
fact, that the package tree is not calculated for filtered roots.

The filter specification may use shell globbing patterns. As a special
extension there is also a negative match if the pattern starts with a "!". Such
patterns will filter out entries that have been otherwise included by previous
patterns in the list.

Example::

    rootFilter:
        - "foo"
        - "bar"
        - "baz"
        - "!*r"

In the above example the root recipes ``foo`` and ``baz`` are included. The
``bar`` root recipe is included initially but later rejected by the negative
``!*r`` match.

.. _configuration-config-sandbox:

sandbox
~~~~~~~

Type: Sandbox-Dictionary

The default paths, mounts and user identity inside a sandbox are defined by the
:ref:`configuration-recipes-provideSandbox` keyword. The ``sandbox`` section in
the user configuration allows to specify additional mounts, search paths or
override the user identity. The format of the settings is the same as in the
:ref:`configuration-recipes-provideSandbox` keyword.

Example::

    sandbox:
        mount:
            - [ "$HOME/bin", "/mnt" ]
        paths:
            - /mnt
        user: "$USER"

The search paths from ``paths`` are added to ``$PATH`` in reverse order so that
later entries have a higher precedence. In contrast to ``provideSandbox`` *no*
variable substitution is possible for the mounts. The mount paths are still subject to
shell variable expansion when a step using the sandbox *is actually executed*,
though.

The ``user`` key allows to override the user identity inside the sandbox. It
takes precedence over the value specified in
:ref:`configuration-recipes-provideSandbox`. The default is ``nobody`` if
neither setting is given. Other possible values are ``root`` and ``$USER``.
The latter is replaced by the current user ID. No other values are allowed.

The example above will mount the ``bin`` directory of the users home directory
as ``/mnt`` inside the sandbox. The ``/mnt`` directory will be in ``$PATH``
before any other search directory of the sandbox but still after any used tool
(if any). Additionally, the user identity inside the sandbox will be the same
as the current user.

.. _configuration-config-scmDefaults:

scmDefaults
~~~~~~~~~~~

Type: Dict of SCM dicts

Default settings for SCMs are applied if the value is not set in the
recipe. Useable values are marked with `(*)` in (:ref:`configuration-recipes-scm`).

Example::

   scmDefaults:
      git:
         branch: "main"
         singleBranch: True
         retries: 3
      url:
         extract: False

.. _configuration-config-scmOverrides:

scmOverrides
~~~~~~~~~~~~

Type: List of override specifications

SCM overrides allow the user to alter any attribute of SCMs
(:ref:`configuration-recipes-scm`) without touching the recipes. They are quite
useful to change e.g. the server url or to override the branch of some SCMs. Overrides
are applied after string substitution. The general syntax looks like the following::

    scmOverrides:
      -
        match:
          url: "git@acme.com:/foo/repo.git"
        del: [commit, tag]
        set:
          branch: develop
        replace:
          url:
            pattern: "foo"
            replacement: "bar"
        if: !expr |
          "${BOB_RECIPE_NAME}" == "foo"

The ``scmOverrides`` key takes a list of one or more override specifications.
You can select overrides using a ``if`` expression. If ``if`` condition evaluates to true
the override is first matched via pattens that are in the ``match`` section.
All entries under ``match`` must be matching for the override to apply. The
right side of a match entry can use shell globbing patterns.

If an override is matching the actions are then applied in the following order:

 * ``del``: The list of attributes that are removed.
 * ``set``: The attributes and their values are taken, overwriting previous values.
 * ``replace``: Performs a substitution based on regular expressions. This
   section can hold any number of attributes with a ``pattern`` and a
   ``replacement``. Each occurrence of ``pattern`` is replaced by
   ``replacement``.

All overrides values are mangled through :ref:`configuration-principle-subst`. Mangling is
performed during calculation of the checkoutStep so that the full environment for this step is
available for substitution.

When an override is applied the ``overridden`` property of the SCM is set to
true. This property can be used with the ``matchScm`` function in package
queries to find packages whose SCM(s) have been overridden.

.. _configuration-config-share:

share
~~~~~

Packages marked as :ref:`configuration-recipes-shared` can be installed to a
shared location. All projects that use the same shared location can benefit
from sharing such shared packages on the same machine.

Example::

    share:
        path: ~/.cache/bob/pkgs
        quota: "5G"
        autoClean: True

The ``path`` property is required. An initial ``~`` or ``~user`` component is replaced
by the users home directory. By default no ``quota`` is set. The quota can either
be set to ``null`` (explicitly disables quota), a number to give the maximum size
in bytes or a string with optional magnitude suffix. The standard IEC units are
supported (``KiB``, ``MiB``, ``GiB`` and ``TiB``) which can optionally be abbreviated
by leaving out the ``iB`` suffix (e.g. ``G`` for ``GiB``). SI units (base 1000) are
supported too (``KB``,  ``MB``, ``GB``, and ``TB``). The ``autoClean`` property,
which defaults to ``True``, controls the garbage collection on installation time.
If enabled, old and unused packages will be deleted automatically if the quota is
exceeded. To manually clean the shared location call :ref:`bob clean --shared <manpage-clean>`.

This setting only applies for local builds. For Jenkins builds the
``shared.dir`` :ref:`bob-jenkins-extended-options` can be set.

It is advisable to configure the shared package location and quota in the user
configuration file (``~/.config/bob/default.yaml``). This way all projects
built by the user will benefit from this location and the quota is consistently
configured for all projects.

.. _configuration-config-ui:

ui
~~

Type: Dictionary

Specifies options of user interface.

color
    Color mode of console output. Can be also overridden by command line
    option ``--color``.

    ``never``
        No colors in output

    ``always``
        Use colors in output

    ``auto``
        Use colors only when TTY console detected (default)

parallelTUIThreshold
    Set the threshold for switching between TUIs. Default: 16

    If the number of jobs exceeds this threshold the TUI switches from one
    status line per job to a TUI using only two status lines.

queryMode
    Set the behaviour of package queries when no package is matched. Can be
    overridden on the command line by the global ``--query`` option of
    :ref:`manpage-bob`.

    ``nullset``
        Empty sets of packages are considered a regular result and never
        treated as an error. This includes trivial path location steps where
        exact package names do not match.

    ``nullglob``
        Return an empty set of packages if the query involves wildcard name
        matches and/or predicates. Otherwise, that is if only direct name
        matches are used, an error is raised if a package name in the path does
        not match. This is the default.

    ``nullfail``
        An empty set of packages is always treated as an error.

.. _configuration-config-whitelist:

whitelist
~~~~~~~~~

Type: List of Strings

Specifies a list of environment variable keys that should be passed unchanged
to all scripts during execution. The content of these variables are considered
invariants of the build. It is no error if any variable specified in this list
is not set. By default the following environment variables are passed to all
scripts:

* Linux and other POSIX platforms: ``PATH``, ``TERM``, ``SHELL``, ``USER``, ``HOME``
* Windows: ``ALLUSERSPROFILE``, ``APPDATA``, ``COMMONPROGRAMFILES``,
  ``COMMONPROGRAMFILES(X86)``, ``COMSPEC``, ``HOMEDRIVE``, ``HOMEPATH``,
  ``LOCALAPPDATA``, ``PATH``, ``PATHEXT``, ``PROGRAMDATA``, ``PROGRAMFILES``,
  ``PROGRAMFILES(X86)``, ``SYSTEMDRIVE``, ``SYSTEMROOT``, ``TEMP``, ``TMP``,
  ``WINDIR``
* MSYS2: Union of POSIX and Windows white list

The names given with ``whitelist`` are *added* to the list and does not replace
the default list.

Example::

   # Keep ssh-agent working
   whitelist: ["SSH_AGENT_PID", "SSH_AUTH_SOCK"]

.. _configuration-config-whitelistremove:

whitelistRemove
~~~~~~~~~~~~~~~

Type: List of strings

Remove the given names from the ``whitelist``. It is not an error to remove a
non-existing name. See :ref:`configuration-config-whitelist` for more details.
