Configuration
=============

When building packages Bob executes the instructions defined by the recipes.
All recipes are located relative to the working directory in the ``recipes``
subdirectory. Recipes are YAML files with a defined structure. The name of the
recipe and the resulting package(s) is derived from the file name by removing
the trailing '.yaml'. To aid further organization of the recipes they may be
put into subdirectories under ``recipes``. The directory name gets part of the
package name with a ``::``-separator.

To minimize repetition of common functionality there is also an optional
``classes`` subdirectory.  Classes have the same structure as recipes and can
be included from recipes and other classes to factor out common stuff. Files
that do not have the '.yaml' extension are ignored when parsing the recipes and
classes directories.

There are two additional configuration files: ``config.yaml`` and
``default.yaml``. The former contains static configuration options while the
latter holds some options that can have a default value and might be overridden
on the command line. Putting that all together a typical recipe tree looks like
the following::

    .
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

Such a recipe and configuration tree is meant to be handled by an SCM. As you
can see in the above tree there is a ``toolchain`` subdirectory in the recipes.
The packages in this directory will be named
``toolchain::arm-linux-gnueabihf``, ``toolchain::make`` and ``toolchain::x86``.
You can also see that there are other files (initramfs/...) that are included
by recipes but are otherwise ignored by Bob.

Principle operation
-------------------

All packages are built by traversing the recipe tree starting from one or more
root recipes. These are recipes that have the ``root`` attribute set to
``True``. There must be at least one root recipe in a project. The tree of
recipes is traversed depth first. While following the dependencies Bob keeps a
local state that consists of the following information:

Environment
    Bob always keeps the full set of variables but only a subset is visible
    when executing the scripts. Initially only the variables defined in
    ``default.yaml`` in the ``environment`` section and whitelisted variables
    named by ``whitelist`` are available. Environment variables can be set at
    various points that are described below in more detail.

Tools
    Tools are aliases for paths to executables. Initially there are no tools.
    They are defined by ``provideTools`` and must be explicitly imported by
    upstream recipes by listing ``tools`` in the ``use`` attribute. Like
    environment variables the tools are kept as key value pairs where the key
    is a string and the value is the executable and library paths that are
    imported when using a tool.

Sandbox
    This defines the root file system and paths that are used to build the
    package.  Unless a sandbox is consumed by listing ``sandbox`` in the
    ``use`` attribute of a dependency the normal host executables are used.
    Sandboxed builds are described in a separate section below.

All of this information is carried as local state when traversing the
dependency tree. Each recipe gets a local copy that is propagated downstream.
Any updates to upstream recipes must be done by explicitly offering the
information with one of the ``provide*`` keywords and the upstream recipe must
consume it by adding the relevant item to the ``use`` attribute of the
dependency.

Step execution
~~~~~~~~~~~~~~

The actual work when building a package is done in the following three steps.
They are Bash scripts that are executed with (and only with) the declared
environment and tools.

Checkout
    The checkout step is there to fetch the source code or any external input
    of the package. Despite the script defined by ``checkoutScript`` Bob
    supports a number of source code management systems natively. They can be
    listed in ``checkoutSCM`` and are fetched/updated before the
    ``checkoutScript`` is run.

Build
    This is the step where most of the work should be done to build the
    package. The ``buildScript`` receives the result of the checkout step as
    argument ``$1`` and any further dependency whose result is consumed is
    passed in order starting with ``$2``. If no checkout step was provided
    ``$1`` will point to some invalid path.

Package
    Typically the build step will produce a lot of intermediate files (e.g.
    object files). The package step has the responsibility to distill a clean
    result of the package. The ``packageScript`` will receive a single argument
    with the patch to the build step.

Each step of a recipe is executed separately and always in the above order. The
scripts working directory is already where the result is expected. The scripts
should make no assumption about the absolute path or the relative path to other
steps. Only the working directory might be modified.

Environment handling
~~~~~~~~~~~~~~~~~~~~

The available set of environment variables starts only with the ones named
explicitly by ``whitelist`` in ``config.yaml``. The next step is to set all
variables listed in ``environment`` to their configured value. The user might
additionally override or set certain variables from the command line. The
so calculated set of variables is the starting point for each root recipe.

The next steps are repeated for each recipe as the dependency tree is traversed.
A copy of the environment is inherited from the upstream recipe.

1. Any variable defined in ``environment`` is set to the given value.
2. Make a copy of the local environment that is subsequently passed to each
   dependency (named "forwarded environment" thereafter).
3. For each dependency do the following:

   a. Make a dedicated copy of the environment for the dependency.
   b. Set variables given in the ``environment`` attribute of the dependency
      in this copy.
   c. Descent to the dependency recipe with the that environment.
   d. Merge all variables of the ``provideVars`` section of the dependency
      into the local environment if ``environment`` is listed in the ``use``
      attribute of the dependency.
   e. If the ``forward`` attribute of the dependency is ``True`` then any
      merged variable of the previous step is updated in the forwarded
      environment too.

A subset of the resulting local environment can be passed to the three
execution steps. The available variables to the scripts are defined by
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

A recipe might optionally offer some variables to the upstream recipe with a
``provideVars`` section. The values of these variables might use variable
substitution where the substituted values are coming from the local
environment. The upstream recipe must explicitly consume these provided
variables by adding ``environment`` to the ``use`` attribute of the dependency.

Tool handling
~~~~~~~~~~~~~

Tools are handled very similar to environment variables when being passed in
the recipe dependency tree. Tools are aliases for a package together with a
relative path to the executable(s) and optionally some library paths for shared
libraries. Another recipe using a tool gets the path to the executable(s) added
to its ``$PATH``.

Starting at the root recipe there are no tools. The next steps are repeated
for each recipe as the dependency tree is traversed. A copy of the tool
aliases is inherited from the upstream recipe.

#. Make a copy of the local tool aliases that is subsequently passed to each
   dependency (named "forwarded tools" thereafter).
#. For each dependency do the following:

   a. Descent to the dependency recipe with the forwarded tools
   b. Merge all tools of the ``provideTools`` section of the dependency into
      the local tools if ``tools`` is listed in the ``use`` attribute of the
      dependency.
   c. If the ``forward`` attribute of the dependency is ``True`` then any
      merged tools of the previous step is updated in the forwarded tools too.

While the full set of tools is carried through the dependency tree only a
specified subset of these tools is available when executing the steps of a
recipe.  The available tools are defined by {checkout,build,package}Tools. A
tool that is consumed in one step is also set in the following. This means a
tool consumed through checkoutTools is also available during the build and
package steps. Likewise, a tool consumed by buildTools is available in the
package step too.

To define one or more tools a recipe must include a ``provideTools`` section
that defines the relative execution path and library paths of one or more tool
aliases. These aliases may be picked up by the upstream recipe by having
``tools`` in the ``use`` attribute of the dependency.

Sandbox operation
~~~~~~~~~~~~~~~~~

Unless a sandbox is configured for a recipe the steps are executed directly on
the host. Bob adds any consumed tools to the front of ``$PATH`` and controls
the available environment variables. Apart from this the build result is pretty
much dependent on the installed applications of the host.

By utilizing `user namespaces`_ on Linux Bob is able to execute the package
steps in a tightly controlled and reproducible environment. This is key to
enable binary reproducible builds. The sandbox image itself is also represented
by a recipe in the project.

.. _user namespaces: http://man7.org/linux/man-pages/man7/user_namespaces.7.html

Initially no sandbox is defined. A downstream recipe might offer its built
package as sandbox through ``provideSandbox``. The upstream recipe must define
``sandbox`` in the ``use`` attribute of this dependency to pick it up as
sandbox. This sandbox is effective only for the current recipe. If ``forward``
is additionally set to ``True`` the following dependencies will inherit this
sandbox for their execution.

Inside the sandbox the result of the consumed or inherited sandbox image is
used as root file system. Only direct inputs of the executed step are visible.
Everything except the working directory and ``/tmp`` is mounted read only to
restrict side effects. The only component used from the host is the Linux
kernel and indirectly Python because Bob is written in this language. The
sandbox image must provide everything to execute the steps. In particular the
following things must be provided by the sandbox image:

* There must be a ``etc/passwd`` file containing the "nobody" user with uid
  65534.
* There must *not* be a ``home`` directory. Bob creates this directory on
  demand and will fail if it already exists.
* There must *not* be a ``tmp`` directory for the same reason.
* At least bash 4 must be installed as ``bin/bash``. Bob uses associative
  arrays that are not available in earlier versions.

.. _configuration-principle-subst:

String substitution
~~~~~~~~~~~~~~~~~~~

At most places where strings are handled in keywords it is possible to use
variable substitution. These substitutions might be simple variables but also a
variety of string processing functions are available that can optionally be
extended by plugins. The following syntax is supported:

* Variable substitution
    * ``${var}``: The value of ``var`` is substituted. The variable has to be
      defined or an error will be raised. Unlike unix shells the braces are
      always required.
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
  to the arguments. To put a comma or closing brace into an argument it has to
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
  are optional. The only supported flag by now is ``i`` to ignore case while
  searching.
* ``$(if-then-else,condition,then,else)``: The expansion of ``condition`` is
  interpreted as boolean value. If the contition is true the expansion of
  ``then`` is returned. Otherwise ``else`` is returned.
* ``$(is-sandbox-enabled)``: Return ``true`` if a sandbox is enabled in the
  current context, ``false`` otherwise.
* ``$(is-tool-defined,name)``: If ``name`` is a defined tool in the current
  context the function will return ``true``. Otherwise ``false`` is returned.
* ``$(ne,left,right)``: Returns ``true`` if the expansions of ``left`` and
  ``right`` differ, otherwise ``false`` is returned.
* ``$(not,condition)``: Interpret the expansion of ``condition`` as boolean
  value and return the opposite.
* ``$(or,condition1,condition2,...)``: Expand each condition and then interpret
  each condition as boolean.  Return ``true`` when the first is true, otherwise
  ``false``.
* ``$(and,condition1,condition2,...)``: Expand each condition and the interpret
  each condition as booelan. Rreturn ``false`` when the first is false,
  otherwise ``true``.
* ``$(strip,text)``: Remove leading and trailing whitespaces from the expansion
  of ``text``.
* ``$(subst,from,to,text)``: Replace every occurence of ``from`` with ``to`` in
  ``text``.

Plugins may provide additional functions as described in
:ref:`extending-hooks-string`. If a string is interpreted as a boolean then the
empty string, "0" and "false" (case insensitive) are considered as logical
"false".  Any other value is considered as "true".

Recipe and class keywords
-------------------------

{checkout,build,package}Script
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Type: String

This is the bash script that is executed by Bob at the respective stage when
building the Packet. It is strongly recommended to write the script as a
newline preserving block literal. See the following example (note the pipe
symbol on the end of the first line)::

    buildScript: |
        ./configure
        make

The script is subject to file inclusion with the ``$<<path>>`` and
``$<'path'>`` syntax. The files are included relative to the current recipe.
The given ``path`` might be a shell globbing pattern. If multiple files are
matched by ``path`` the files are sorted by name and then concatenated. The
``$<<path>>`` syntax imports the file(s) as is and replaces the escape pattern
with a (possibly temporary) file name which has the same content. Similar to
that, the ``$<'path'>`` syntax includes the file(s) inline as a quoted string.
In any case the strings are fully quoted and *not* subject to any parameter
substitution.

.. note::
   When including files as quoted strings (``$<'path'>`` syntax) they have to
   be UTF-8 encoded.

The scripts of any classes that are inherited which define
a script for the same step are joined in front of this script in the order the
inheritance is specified. The inheritance graph is traversed depth first and
every class is included exactly once.

During execution of the script only the environment variables SHELL, USER,
TERM, HOME and anything that was declared via {checkout,build,package}Vars
are set. The PATH is reset to "/usr/local/bin:/bin:/usr/bin" or whatever was declared
in config.yaml. Any tools that
are consumed by a {checkout,build,package}Tools declaration are added to the
front of PATH. The same holds for ``$LD_LIBRARY_PATH`` with the difference of starting
completely empty.

Additionally the following variables are populated automatically:

* ``BOB_CWD``: The working directory of the current script.
* ``BOB_ALL_PATHS``: An associative array that holds the paths to the results
  of all dependencies indexed by the package name. This includes indirect
  dependencies such as consumed tools or the sandbox too.
* ``BOB_DEP_PATHS``: An associative array of all direct dependencies. This
  array comes in handy if you want to refer to a dependency by name (e.g.
  ``${BOB_DEP_PATHS[libfoo-dev]}``) instead of the position (e.g. ``$2``).
* ``BOB_TOOL_PATHS``: An associative array that holds the execution paths to
  consumed tools indexed by the package name. All these paths are in ``$PATH``.

{checkout,build,package}Tools
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Type: List of strings

This is a list of tools that should be added to ``$PATH`` during the execution
of the respective checkout/build/package script. A tool denotes a folder in an
(indirect) dependency. A tool might declare some library paths that are then
added to ``$LD_LIBRARY_PATH``.  The order of tools in ``$PATH`` and
``$LD_LIBRARY_PATH``  is unspecified.  It is assumed that each tool provides a
separate set of executables so that the order of their inclusion does not
matter.

A tool that is consumed in one step is also set in the following. This means a
tool consumed through checkoutTools is also available during the build and
package steps. Likewise a tool consumed by buildTools is available in the
package step too. The rationale is that all three steps form a small pipeline.
If a step depends on a certain tool then the result of the following step is
already indirectly dependent on this tool. Thus it can be available during the
following step anyway.


.. _configuration-recipes-vars:

{checkout,build,package}Vars
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Type: List of strings

This is a list of environment variables that should be set during the execution
of the checkout/build/package script. This declares the dependency of the
respective step to the named variables. You may use shell patterns (e.g.
``CONFIG_*``) to match multiple variables.

It is not an error that a variable listed here is unset. This is especially
useful for classes or to implement default behaviour that can be overridden by
the user from the command line. If you expect a variable to be unset it is your
responsibility to handle that case in the script. Every reference to such a
variable should be guarded with ``${VAR-somthing}`` or ``${VAR+something}``.

A variable that is consumed in one step is also set in the following. This
means a variable consumed through checkoutVars is also set during the build
and package steps. Likewise, a variable consumed by buildVars is set in the
package step too. The rationale is that all three steps form a small pipeline.
If a step depends on a certain variable then the result of the following step
is already indirectly dependent on this variable. Thus it can be set during the
following step anyway.

.. _configuration-recipes-vars-weak:

{checkout,build,package}VarsWeak
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Type: List of strings

This is a list of environment variables that should be set during the execution
of the checkout/build/package script. These variables are not considered to
influence the result, very much like the variables listed in
:ref:`configuration-config-whitelist`. You may use shell patterns (e.g.
``CONFIG_*``) to match multiple variables.

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

checkoutDeterministic
~~~~~~~~~~~~~~~~~~~~~

Type: Boolean

By default any checkoutScript is considered indeterministic. The rationale is
that extra care must be taken for a script to fetch always the same sources. If
you are sure that the result of the checkout script is always the same you may
set this to ``True``. All checkoutSCMs on the other hand are capable of
determining automatically whether they are determinstic.

If the checkout is deemed deterministic it enables Bob to apply various
optimizations.  It is also the basis for binary artifacts.

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
``dir`` and ``if``. By default the SCMs check out to the root of the workspace.
You may specify any relative path in ``dir`` to checkout to this directory.

By using ``if`` you can selectively enable or disable a particular SCM. The
string given to the ``if``-keyword is substituted according to
:ref:`configuration-principle-subst` and the final string is interpreted as a
boolean value (everything except the empty string, ``0`` and ``false`` is
considered true). The SCM will only be considered if the condition passes.

Currently the following ``scm`` values are supported:

=== ============================ =======================================================================================
scm Description                  Additional attributes
=== ============================ =======================================================================================
git `Git`_ project               | ``url``: URL of remote repository
                                 | ``branch``: Branch to check out (optional, default: master)
                                 | ``tag``: Checkout this tag (optional, overrides branch attribute)
                                 | ``commit``: SHA1 commit Id to check out (optional, overrides branch or tag attribute)
                                 | ``rev``: Canonical git-rev-parse revision specification (optional, see below)
svn `Svn`_ repository            | ``url``: URL of SVN module
                                 | ``revision``: Optional revision number (optional)
cvs CVS repository               | ``cvsroot``: repository location ("``:ext:...``", path name, etc.)
                                 | ``module``: module name
                                 | ``rev``: revision, branch, or tag name (optional)
url While not a real SCM it      | ``url``: File that should be downloaded
    allows to download (and      | ``digestSHA1``: Expected SHA1 digest of the file (optional)
    extract) files/archives.     | ``digestSHA256``: Expected SHA256 digest of the file (optional)
                                 | ``extract``: Extract directive (optional, default: auto)
                                 | ``fileName``: Local file name (optional, default: url file name)
=== ============================ =======================================================================================

.. _Git: http://git-scm.com/
.. _Svn: http://subversion.apache.org/

The ``git`` SCM requires at least an ``url`` attribute. The URL might be any
valid Git URL. To checkout a branch other than *master* add a ``branch``
attribute with the branch name. To checkout a tag instead of a branch specify
it with ``tag``. You may specify the commit id directly with a ``commit``
attribute too.

.. note:: The default branch of the remote repository is not used. Bob will
   always checkout "master" unless ``branch``, ``tag`` or ``commit`` is given.

The ``rev`` property of the ``git`` SCM unifies the specification of the
desired branch/tag/commit into one single property. If present it will be
evaluated first. Any other ``branch``, ``tag`` or ``commit`` property is
evalued after it and may override a precious setting made by ``rev``. The
branch/tag/commit precedence is still respected, though. Following the patterns
described in git-rev-parse(1) the following formats are currently supported:

* <sha1>, e.g. dae86e1950b1277e545cee180551750029cfe735.
  The full SHA-1 object name (40-byte hexadecimal string).
* refs/tags/<tagname>, e.g. refs/tags/v1.0.
  The symbolic name of a tag.
* refs/heads/<branchname>, e.g. refs/heads/master.
  The name of a branch.

The Svn SCM, like git, requires the ``url`` attribute too. If you specify a
numeric ``revision`` Bob considers the SCM as deterministic.

The CVS SCM requires a ``cvsroot``, which is what you would normally put in
your CVSROOT environment variable or pass to CVS using ``-d``. If you specify
a revision, branch, or tag name, Bob will check out that instead of the HEAD.
Unfortunately, because Bob cannot know beforehand whether the ``rev`` you gave
it points to a branch or tag, it must consider this SCM nondeterministic.
To check out using ssh, you can use the syntax ``:ssh:user@host:/path``,
which will be translated into an appropriate ``CVS_RSH`` assignment by Bob.
Alternatively, you can use a normal ``:ext:`` CVSROOT and manually pass the
``CVS_RSH`` value into the recipe using ``checkoutVars``.

The ``url`` SCM naturally needs an ``url`` attribute. If a SHA digest is given
with ``digestSHA1`` and/or ``digestSHA256`` the downloaded file will be checked
for a matching hash sum. This also makes the URL deterministic for Bob.
Otherwise the URL will be checked in each build for updates. Based on the file
name ending Bob will try to extract the downloaded file. You may prevent this
by setting the ``extract`` attribute to ``no`` or ``False``. If the heuristic
fails the extraction tool may be specified as ``tar``, ``gzip``, ``xz``, ``7z``
or ``zip`` directly.


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
        -
            name: toolchain
            use: [tools, environment]
            forward: True
        -
            if: "${FOOBAR}"
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
|             |                 |                                                     |
+-------------+-----------------+-----------------------------------------------------+
| if          | String          | The string (subject to substitution) is interpreted |
|             |                 | as boolean value. The dependency is only considered |
|             |                 | if the string is considered as true. See            |
|             |                 | :ref:`configuration-principle-subst`.               |
|             |                 |                                                     |
|             |                 | Default: true                                       |
+-------------+-----------------+-----------------------------------------------------+

.. _configuration-recipes-env:

environment
~~~~~~~~~~~

Type: Dictionary (String -> String)

Defines environment variables in the scope of the current recipe. Any inherited
variables of the upstream recipe with the same name are overwritten. All
variables are passed to downstream recipes.

Example::

   environment:
      PKG_VERSION: "1.2.3"

See also :ref:`configuration-recipes-privateenv`.

filter
~~~~~~

Type: Dictionary ( "environment" | "sandbox" | "tools" -> List of Strings)

The filter keyword allows to restrict the environment variables, tools and
sandboxes inherited from upstream recipes. This way a recipe can effectively
restrict the number of package variants.

The filters specifications may use shell globbing patterns. As a special
extension there is also a negative match if the pattern starts with a "!". Such
patterns will filter out entries that have been otherwise included by previous
patterns in the list (e.g. by inherited classes).

Example::

    filter:
        environment: [ "*_MIRROR" ]
        tools: [ "*toolchain*", "!host-toolchain" ]
        sandbox: [ "*" ]

In the above example the recipe would inherit only environment variables that
end with "_MIRROR". All other variables are unset. Likewise all tools that have
"toolchain" in their name are inherited, except the "host-toolchain". Anything
is accepted as sandbox which would also be the default if left out.

.. warning::
   The filter keyword is still experimental and may change in the future or
   might be removed completely.


inherit
~~~~~~~

Type: List of Strings

Include classes with the given name into the current recipe. Example::

   inherit: [cmake]

Classes are searched in the ``classes/`` directory with the given name. The
syntax of classes is the same as the recipes. In particular classes can inherit
other classes too. The inheritance graph is traversed depth first and every
class is included exactly once.

All attributes of the class are merged with the attributes of the current
recipe. If the order is important the attributes of the class are put in front
of the respective attributes of the recipe. For example the scripts of the
inherited class of all steps are inserted in front of the scripts of the
current recipe. 


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

Type: Dictionary (String -> String)

Defines environment variables just for the current recipe. Any inherited
variables with the same name of the upstream recipe or others that were
consumed from the dependencies are overwritten. All variables defined or
replaced by this keyword are private to the current recipe.

Example::

   privateEnvironment:
      APPLY_FOO_PATCH: "no"

See also :ref:`configuration-recipes-env`.

.. _configuration-recipes-providedeps:

provideDeps
~~~~~~~~~~~

Type: List of Patterns

The ``provideDeps`` keyword receives a list of dependency names. These must be
dependencies of the current recipe, i.e. they must appear in the ``depends``
section. It is no error if the condition of such a dependency evaluates to
false. In this case the entry is silently dropped. To specify multiple
dependencies with a single entry shell globbing patterns may be used.

Provided dependencies are subsequently injected into the dependency list of the
upstream recipe that has a dependency to this one (if ``deps`` is included in
the ``use`` attribute of the dependency, which is the default). This works in a
transitive fashion too, that is provided dependencies of a downstream recipe
are forwarded to the upstream recipe too.

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

The ``path`` attribute is always needed.  The ``libs`` attribute, if present,
must be a list of paths to needed shared libraries. Any path that is specified
must be relative. If the recipe makes use of existing host binaries and wants
to provide them as tool you should create symlinks to the host paths.

If no library paths are present the declaration may be abbreviated by giving
the relative path directly::

   provideTools:
      host-toolchain: bin

provideVars
~~~~~~~~~~~

Type: Dictionary (String -> String)

Declares arbitrary environment variables with values that should be passed to
the upstream recipe. The values of the declared variables are subject to
variable substitution. The substituted values are taken from the current
package environment. Example::

    provideVars:
        ARCH: "arm"
        CROSS_COMPILE: "arm-linux-${ABI}-"


By default these provided variables are not picked up by upstream recipes. This
must be declared explicitly by a ``use: [environment]`` attribute in the
dependency section of the upstream recipe. Only then are the provided variables
merged into the upstream recipes environment.

provideSandbox
~~~~~~~~~~~~~~

Type: Sandbox-Dictionary

The ``provideSandbox`` keyword offers the current recipe as sandbox for the
upstream recipe. Any consuming upstream recipe (via ``use: [sandbox]``) will
be built in a sandbox where the root file system is the result of the current
recipe. The initial ``$PATH`` is defined with the required ``paths`` keyword
that should hold a list of paths. This will completely replace ``$PATH`` of
the host for consuming recipes.

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

Variable substitution is possible for the paths. The paths are also subject to
shell variable expansion when a step using the sandbox *is actually executed*.
This can be usefull e.g. to expand variables that are only available on the
build server. Example::

    provideSandbox:
        paths: ["/bin", "/usr/bin"]
        mount:
            - "/etc/resolv.conf"
            - "${MYREPO}"
            - "\\$HOME/.ssh"
            - ["\\$SSH_AUTH_SOCK", "\\SSH_AUTH_SOCK", [nofail, nojenkins]]

The example assumes that the variable ``MYREPO`` was set somewhere in the
recipes.  On the other hand ``$HOME`` is expanded later by the shell. This is
quite usefull on Jenkins because the home directory there is certainly
different from the one where Bob runs. The last entry shows two mount option
being used. This line mounts the ssh-agent socket into the sandbox if
available. This won't be done on Jenins at all and the build will proceed even
if ``$SSH_AUTH_SOCK`` is unset or invalid. Note that such variables have to be
in the :ref:`configuration-config-whitelist` to be available to the shell.

.. note::
    The mount paths are considered invariants of the build. That is changing the
    mounts will neither automatically cause a rebuild of the sandbox (and affected
    packages) nor will binary artifacts be re-fetched.

root
~~~~

Type: Boolean

Recipe attribute which defaults to False. If set to True the recipe is declared
a root recipe and becomes a top level package. There must be at least one root
package in a project.

.. _configuration-recipes-shared:

shared
~~~~~~

Type: Boolean

Marking a recipe as shared implies that the result may be shared between
different projects or workspaces. Only completely deterministic packages may be
marked as such. Typically large static packages (such as toolchains) are
enabled as shared packages. By reusing the result the hard disk usage can be
sometimes reduced drastically.

The exact behaviour depends on the build backend. Currently the setting has no
influence on local builds. On Jenkins the result will be copied to a separate
directory in the Jenkins installation and will be used from there. This reduces
the job workspace size considerably at the expense of having artifacts outside
of Jenkins's regular control.

.. _configuration-config:

Project configuration (config.yaml)
-----------------------------------

The file ``config.yaml`` holds all static configuration options that are not
subject to be changed when building packages. The following sections describe
the top level keys that are currently understood. The file is optional or could
be empty.

bobMinimumVersion
~~~~~~~~~~~~~~~~~

Type: String

Defines the minimum required version of Bob that is needed to build this
project. Any older version will refuse to build the project. The version number
given here might be any prefix of the actual version number, e.g. "0.1" instead
of the actual version number (e.g. "0.1.42"). Bob's version number is specified
according to `Semantic Versioning`_. Therefore it is usually only needed to
specify the major and minor version.

.. _Semantic Versioning: http://semver.org/

.. _configuration-config-plugins:

plugins
~~~~~~~

Type: List of strings

Plugins are loaded in the same order as listed here. For each name in this
section there must be a .py-file in the ``plugins`` directory next to the
recipes. For a detailed description of plugins see :ref:`extending-plugins`.

User configuration (default.yaml)
---------------------------------

The ``default.yaml`` file holds configuration options that may be overridden by
the user. Most commands will also take an '-c' option where any number of
additional configuration files with the same syntax can be specified.

User configuration files may optionally include other configuration files.
These includes are parsed *after* the current file, meaning that options of
included configuration files take precedence over the current one. Included
files do not need to exist and are silently ignored if missing. Includes are
specified without the .yaml extension::

    include:
        - overrides

environment
~~~~~~~~~~~

Type: Dictionary (String -> String)

Specifies default environment variables. Example::

   environment:
      # Number of make jobs is determined by the number of available processors
      # (nproc).  If desired it can be set to a specific number, e.g. "2". See
      # classes/make.yaml for details.
      MAKE_JOBS: "nproc"

.. _configuration-config-whitelist:

whitelist
~~~~~~~~~

Type: List of Strings

Specifies a list of environment variable keys that should be passed unchanged
to all scripts during execution. The content of these variables are considered
invariants of the build. It is no error if any variable specified in this list
is not set. By default the following environment variables are passed to all
scripts: ``TERM``, ``SHELL``, ``USER`` and ``HOME``. The names given with
``whitelist`` are *added* to the list and does not replace the default list.

Example::

   # Keep ssh-agent working
   whitelist: ["SSH_AGENT_PID", "SSH_AUTH_SOCK"]

archive
~~~~~~~

Type: Dictionary or list of dictionaries

The ``archive`` key configures the default binary artifact server(s) that
should be used. It is either directly an archive backend entry or a list of
archive backends. For each entry at least the ``backend`` key must be
specified. Optionally there can be a ``flags`` key that receives a list of
various flags, in particular for what operations the backend might be used. See
the following list for possible flags. The default is ``[download, upload]``.

``download``
    Use this archive to download artifacts. Note that you still have to
    explicitly enable downloads on Jenkins servers. For local builds the exact
    download behaviour depends on the build mode (release vs. develop).

``upload``
    Use this archive to upload artifacts. To actually upload to the archive the
    build must be performed with uploads enabled (``--upload``).

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
file        Use a local directory as binary artifact repository. The directory
            is specified in the ``path`` key as absolute path.
http        Uses a HTTP server as binary artifact repository. The server has to
            support the HEAD, PUT and GET methods. The base URL is given in the
            ``url`` key.
shell       This backend can be used to execute commands that do the actual up-
            or download. A ``download`` and/or ``upload`` key provides the
            commands that are executed for the respective operation. The
            configured commands are executed by bash and are expected to copy
            between the local archive (given as ``$BOB_LOCAL_ARTIFACT``) and
            the remote one (available as ``$BOB_REMOTE_ARTIFACT``). See the
            example below for a possible use with ``scp``.
=========== ===================================================================

The directory layouts of the ``file``, ``http`` and ``shell``
(``$BOB_REMOTE_ARTIFACT``) backends are compatible. If multiple download
backends are available they will be tried in order until a matching artifact is
found. All available upload backends are used for uploading artifacts. Any
failing upload will fail the whole build.

.. note::
   The uploaded artifacts do not have any metadata attached to them yet.
   Despite the opaque Build-ID there is no information, e.g. about the recipes
   that created this artifact. Therefore there are no tools to manage binary
   artifacts yet.

Example::

   archive:
      backend: http
      url: "http://localhost:8001/upload"

It is also possible to use separate methods for upload and download::

    archive:
        -
            backend: http
            url: "http://localhost:8001/archive"
            flags: [download]
        -
            backend: shell
            upload: "scp -q ${BOB_LOCAL_ARTIFACT} localhost:archive/${BOB_REMOTE_ARTIFACT}"
            download: "scp -q localhost:archive/${BOB_REMOTE_ARTIFACT} ${BOB_LOCAL_ARTIFACT}"
            flags: [upload]

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

The ``scmOverrides`` key takes a list of one or more override specifications.
The override is first matched via patterns that are in the ``match`` section.
All entries under ``match`` must be matching for the override to apply. The
right side of a match entry can use shell globbing patterns.

If an override is matching the actions are then applied in the following order:

 * ``del``: The list of attributes that are removed.
 * ``set``: The attributes and their values are taken, overwriting previous values.
 * ``replace``: Performs a substitution based on regular expressions. This
   section can hold any number of attributes with a ``pattern`` and a
   ``replacement``. Each occurrence of ``pattern`` is replaced by
   ``replacement``.

alias
~~~~~

Type: Dictionary (String -> String)

Specifies alias names for packages::

   alias:
      myApp: "host/files/group/app42"
