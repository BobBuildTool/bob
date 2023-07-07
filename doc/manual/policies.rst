.. _policies:

Policies
========

The Bob Policy mechanism provides backwards compatibility as a first-class
feature. Most of the general documentation below is taken from `CMake`_ `(CC BY
2.5)`_ as the design goals are identical.

.. _CMake: https://cmake.org/Wiki/CMake/Policies
.. _(CC BY 2.5): https://creativecommons.org/licenses/by/2.5/


Motivation
----------

Bob is an evolving project. The developers strive to support existing projects
as much as possible as changes are made. Unfortunately there are some cases
where it is not possible to fix bugs and preserve backwards compatibility at
the same time. The Bob policies try to make the transition as smooth as
possible without stalling the development of the tool due to legacy design
decisions or bugs.

Design Goals
------------

The design goals for the Bob Policy mechanism were as follows:

1. Existing projects should build with versions of Bob newer than that used
   by the project authors

   * Users should not need to edit code to get the projects to build
   * Warnings may be issued but the projects should build

2. Correctness of new interfaces or bugs fixed in old ones should not be
   inhibited by compatibility requirements

   * Any reduction in correctness of the latest interface is not fair to new
     projects

3. Every change to Bob that may require changes to project code should be
   documented

   * Each change should also have a unique identifier that can be referenced by
     warning and error messages
   * The new behavior is enabled only when the project has somehow indicated it
     is supported

4. We must be able to eventually remove code implementing compatibility with
   ancient Bob versions

   * Such removal is necessary to keep the code clean and allow internal
     refactoring
   * After such removal attempts to build projects written for ancient versions
     must fail with an informative message

Solution
--------

We've introduced the notion of a policy for dealing with changes in Bob
behavior. Each policy has:

* a unique name,
* a disabled (old) behavior that preserves compatibility with earlier versions
  of Bob,
* an enabled (new) behavior that is considered correct and preferred for use
  by new projects,
* documentation detailing the motivation for the change and the old and new
  behaviors.

Projects may configure the setting of each policy to request old (disabled) or
new (enabled) behavior. When Bob encounters recipes that may be affected by a
particular policy it checks to see whether the project has set the policy. If
the policy has been set (enabled or disabled) then Bob follows the behavior
specified. If the policy has not been set then the old behavior is used but a
warning is produced telling the project author to set the policy.

Setting Policies
----------------

Policies can be set in ``config.yaml``. They are either set implicitly by
:ref:`configuration-bobMinimumVersion` or explicitly in the
:ref:`configuration-config-policies` section.

In most cases a project release should simply set a policy version
corresponding to the release version of Bob for which the project is written.
Setting the policy version requests new behavior for all policies introduced in
the corresponding version of Bob or earlier. Policies introduced in later
versions are marked as not set in order to produce proper warning messages.
The policy version is set using the :ref:`configuration-bobMinimumVersion` key
in ``config.yaml`` of the project.

For example, the configuration::

    bobMinimumVersion: "0.13"

will request new behavior for all policies introduced in Bob 0.13 or earlier.
Of course one should replace "0.13" with a higher version as necessary.

When a new version of Bob is released that introduces new policies it will
still build old projects because they do not request new behavior for any of
the new policies. When starting a new project one should always specify the
most recent release of Bob to be supported as the policy version level. This
will make sure that the project is written to work using policies from that
version of Bob and not using any old behavior.

Additionally, each policy may be set individually to help project authors
incrementally convert their projects to use new behavior or silence warnings
about dependence on old behavior. The :ref:`configuration-config-policies`
section in ``config.yaml`` may be used to explicitly request old or new
behavior for a particular policy. This overrides any defaults that were set by
:ref:`configuration-bobMinimumVersion`.

.. _policies-defined:

Defined policies
----------------

.. _policies-relativeIncludes:

relativeIncludes
~~~~~~~~~~~~~~~~

Introduced in: 0.13

User configuration files (e.g. ``default.yaml`` or files passed by ``-c`` on
the command line) can include other configuration files in the ``include``
section. Versions of Bob before 0.13 included these files always relative to
the root of the project configuration.

Starting with Bob 0.13 it is possible to have global and user specific
configuration files too. To allow inclusion of further files from these
configuration files the include location was changed to "file relative"
includes. That is, any included file is seared relative to the currently
processed file.

Old behaviour
    Include further files from ``default.yaml`` and command line passed files
    relative to the project root directory. Global configuration files use the
    new policy in any case.

New behaviour
    All files are included relative to the currently processed file.

.. _policies-cleanEnvironment:

cleanEnvironment
~~~~~~~~~~~~~~~~

Introduced in: 0.13

The environment variables that are consumed in recipes are fundamentally
calculated from the recipes only. Bob has the notion of white listed variables
that shall not influence the build result but should still be set during
execution. Their value is kept unchanged from the current OS environment when
building packages.

Previously the current set of environment variables during package calculation
started with the ones named by :ref:`configuration-config-whitelist` in
``default.yaml``. This made these variables bound to the value that was set
during package calculation. Especially on Jenkins setups this is wrong as the
machine that configures the Jenkins may have a different OS environment than
the Jenkins executors/slaves. Also using such variables in the recipes made
the calculated packages dependent on the state of the local machine.

Old behavior
    Environment computation in root recipes starts with white listed variables
    of the current OS environment.

New behavior
    Package computation starts with a clean environment. The default
    environment variables (:ref:`configuration-config-environment`) may
    reference OS environment variables and are taken as initial environment for
    package computation. White listed variables are only available while
    building packages and are taken verbatim from the current OS execution
    environment.

.. _policies-tidyUrlScm:

tidyUrlScm
~~~~~~~~~~

Introduced in: 0.14

Historically the URL SCM was not tracking the checkout directory but the individual
files that are downloaded by the SCM. This has the advantage that it is possible
to download more than one file into the same directory. There are a couple of
major disadvantages, though:

1. When extracting multiple archives in the same directory it might be possible
   that some files are overwritten.
2. Any extracted files are not tracked by Bob and will be left untouched in
   develop mode when the recipe is updated. This leads to stale files in the
   src-directory and will typically prevent that matching binary artifacts are
   found.
3. Trying to reliably apply patches across SCM updates is tricky because files
   are only overwritten and never garbage collected.

Starting with 0.14 Bob will manage the whole checkout directory. This unifies
the behaviour with the other SCMs and solves the above disadvantages. This
change might break existing projects because with the new behaviour it is not
possible to put multiple URL SCMs into the same directory.

Old behavior
    Bob tracks only the downloaded file across recipe updates. Upon changes only
    the involved file is moved away and the new one is downloaded. Extracted
    files from archives stay in workspace.

New behavior
    The whole directory where the URL SCM is checked out is tracked by Bob.
    Changing the recipe will move away the whole checkout directory, including
    any possibly extracted files.

.. _policies-allRelocatable:

allRelocatable
~~~~~~~~~~~~~~

Introduced in: 0.14

When up- or downloading binary artifacts Bob has to make sure that the artifact
is independent of the actual location in the file system. This is not always
the case for tools that are executed on the build host. Historically Bob
assumed that all packages that were created from recipes that define at least
one tool are not relocatable. Such packages were not up- or downloaded except
when building in a sandbox because the sandbox virtualises the paths and makes
them deterministic everywhere.

Starting with Bob 0.14 the :ref:`configuration-recipes-relocatable` property
allows to specify this more fine grained. To not break existing recipes the
``relocatable`` property has a default value compatible to the old behaviour
described above. Because this heuristic is quite pessimistic and almost always
wrong the ``allRelocatable`` policy switches the default to *always
relocatable*.

Old behavior
    The default value of the :ref:`configuration-recipes-relocatable` property
    is ``True`` unless the recipe defines at least one tool. In this case the
    default value is ``False``.

New behavior
    The default value of the :ref:`configuration-recipes-relocatable` property
    is always ``True``.

Starting with Bob 0.15 the new behavior will also enable fingerprinting if a
fingerprint script has been defined. In case of a non-relocatable package the
fingerprint will additionally encode the workspace path. This enables safe
artifact exchange even outside of a sandbox.

.. _policies-offlineBuild:

offlineBuild
~~~~~~~~~~~~

Introduced in: 0.14

Bob assumes that build and package steps are always deterministic. It is
therefore usually not a good idea to access the network other than in the
checkout step where the external source code is fetched. Bob has the ability to
isolate the network when building a package in a sandbox. If the network must
still be accessible during build and/or package steps the recipe might set the
respective properties (see :ref:`configuration-recipes-netAccess`).

Old behavior
    External network access is always possible.

New behavior
    During checkout steps the external network is always accessible. When
    building inside a sandbox the network will be isolated during build and
    package steps by default. A recipe might override this to still allow
    network access if required.

.. _policies-sandboxInvariant:

sandboxInvariant
~~~~~~~~~~~~~~~~

Introduced in: 0.14

Traditionally the impact of a sandbox to the build has not been handled
consistently. On one hand the actual usage of a sandbox was not relevant for
binary artifacts. As such, an artifact that was built inside a sandbox was also
used when building without the sandbox (and vice versa). On the other hand Bob
did rebuild everything from scratch when switching between sandbox/non-sandbox
builds. This inconsistent behavior is rectified by the ``sandboxInvariant``
policy that consistently declares builds as invariant of the sandbox.

Old behavior
    The sandbox is handled inconsistently. Bob will use binary artifacts across
    sandbox/non-sandbox builds but will rebuild clean if doing so. Changing the
    sandbox recipe will invalidate binary artifacts even when not using the
    sandbox.

New behavior
    The build result is always an invariant of the sandbox, that is the sandbox
    content and its usage makes no difference for Bob. This means that binary
    artifacts are used across sandbox/non-sandbox builds. Moving between
    sandbox/non-sandbox builds just triggers incremental builds of the affected
    packages. Changing the sandbox content will also trigger just incremental
    builds of affected packages.

In any case a recipe shall produce the same result regardless of the fact that
a sandbox is used or not. This is and has always been a fundamental assumption
of Bob with respect to binary artifacts. If the result of a recipe depends on
the host environment then an appropriate environment variable defined by the
sandbox should be used to let Bob detect this.


.. _policies-uniqueDependency:

uniqueDependency
~~~~~~~~~~~~~~~~

Introduced in: 0.14

Traditionally it was allowed to name a dependency more than once in a recipe.
On the other hand the semantics were not well defined. The result was picked up
only once. Due to the multiple references different variants of the dependency
could be created, though. This was detected only if the result of the
dependencies was used. Otherwise this created unaddressable packages that
cannot be built individually.  It is also possible that, even if the packages
themself are of the same variant, they might provide different dependencies or
variables upwards. This is handled but not easily detectable by the user.

Old behavior
    Listing a dependency more than once in a recipe is tolerated. The result is
    only picked up once, though. Anything else (environment, tools, ...) is
    picked up at each instance again, possibly replacing previous definitions.

New behavior
    A dependency must only be named once. This is enforced *after* evaluating
    the ``if`` condition of the dependencies. It is therefore still possible to
    have multiple references to the same package given that only one reference
    is active. Everything else will result in a parsing error.

.. _policies-mergeEnvironment:

mergeEnvironment
~~~~~~~~~~~~~~~~

Introduced in: 0.15

The :ref:`configuration-recipes-env` and
:ref:`configuration-recipes-privateenv` sections of the recipes and classes it
inherits from are merged when the packages are calculated. Traditionally this
was done on a key-by-key basis without variable substitution. Keys from the
recipe or an inherited class would simply shadow keys from later inherited
classes. This had the effect that the definitions of later inherited classes
were lost. It was also not possible to pick them up via variable substitution.
Suppose the following simple recipe/class structure::

    recipes/foo.yaml:
        inherit: [asan, werror]
        privateEnvironment:
            CFLAGS: "${CFLAGS:-} -DFOO=1"

    classes/asan.yaml:
        privateEnvironment:
            CFLAGS: "${CFLAGS:-} -fsanitize=address"

    classes/werror.yaml:
        privateEnvironment:
            CFLAGS: "${CFLAGS:-} -Werror"

Previously the definition of ``CFLAGS`` in the recipe would completely shadow
the ones of the inherited classes. So the ``CFLAGS`` variable would only ever
be amended with ``-DFOO=1``. In contrast to this unintuitive result the new
behavior is to take all classes into account and merge their values by applying
the usual variable substitution.

Old behavior
    Environment keys in the recipe or earlier inherited classes shadow any
    later inherited classes. Variable substitution is done only with the first
    definition of the key. Any shadowed deviations are not examined. Given the
    above example the resulting ``CFLAGS`` would be ``${CFLAGS:-} -DFOO=1``.

New behavior
    All environment keys are eligible to variable substitution. The definitions
    of the recipe has the highest precedence (i.e. it is substituted last).
    Declarations of classes are substituted in their inheritance order, that is,
    the last inherited class has the highest precedence. Given the above
    example the resulting ``CFLAGS`` would be ``${CFLAGS:-} -fsanitize=address
    -Werror -DFOO=1``

.. _policies-secureSSL:

secureSSL
~~~~~~~~~

Introduced in: 0.15

Due to historical reasons Bob did not check for SSL certificate errors
everywhere. While most parts were already secure the git SCM and HTTPS archive
backend were still insecure by default.

Old behavior
    The git SCM and the HTTPS archive backend do not check for certificate
    errors by default. May still be enabled by setting the corresponding
    ``sslVerify`` option to ``True``.

New behavior
    Whenever a secure connection is used the certificate is checked. May be
    disabled selectively by setting the corresponding ``sslVerify`` option to
    ``False``.

.. _policies-sandboxFingerprints:

sandboxFingerprints
~~~~~~~~~~~~~~~~~~~

Introduced in: 0.16

When :ref:`configuration-principle-fingerprinting` was introduced, Bob
initially used a shortcut and did not execute fingerprint scripts in the
sandbox. This saved a bit of complexity and also relieved the build logic from
the need to build the sandbox just to execute the fingerprint script. While the
old approach was not producing wrong results it was overly pessimistic. It
prevents sharing of any fingerprinted artifacts between sandbox and non-sandbox
builds even if the fingerprint is the same.

Old behavior
   Fingerprint scripts are not executed in sandbox builds. Instead the sandbox
   image as a whole is used as fingerprint. This prevents the exchange of
   fingerprinted artifacts between sandbox- and non-sandbox-builds.

New behaviour
   Bob will execute fingerprint scripts in the sandbox too. Fingerprinted
   artifacts will be shared between sandbox- and non-sandbox-builds given the
   :ref:`configuration-recipes-fingerprintScript` yields the same result.
   Fingerprint results for sandbox builds are cached in the binary artifact
   cache if available. This reduces the need to build the sandbox just to
   calculate the fingerprint.

   Old artifacts that were built in a sandbox will not be found anymore in the
   artifact cache. They will have to be built again. Non-sandbox build
   artifacts are not affected.

.. _policies-fingerprintVars:

fingerprintVars
~~~~~~~~~~~~~~~

Introduced in: 0.16

When then :ref:`configuration-recipes-fingerprintScript` mechanism was
introduced in Bob 0.15 there was no dedicated environment variable handling
implemented for them. The simple policy was to pass all environment variables
of the affected package to the ``fingerprintScript``. Unfortunately this
results in the repeated execution of identical scripts if the variables change
between packages, even if they are not used by the ``fingerprintScript``.

This policy adds the support for the new
:ref:`configuration-recipes-fingerprintVars` key in the recipes. This key
specifies a list of variables that the ``fingerprintScript`` uses.

Old behavior
   All variables of the fingerprinted package are passed to the
   ``fingerprintScript``. The :ref:`configuration-recipes-fingerprintVars`
   settings are ignored. This might lead to unnecessary executions of identical
   ``fingerprintScript`` with different variable values.

New behavior
   Only the subset of environment variables, defined by
   :ref:`configuration-recipes-fingerprintVars` of the fingerprinted package is
   passed to the ``fingerprintScript``. Other environment variables are unset
   but whitelisted variables (see :ref:`configuration-config-whitelist`) are
   still available.

.. _policies-noUndefinedTools:

noUndefinedTools
~~~~~~~~~~~~~~~~

Introduced in: 0.18

It was perfectly valid to list tools in ``{checkout,build,package}Tools`` that
are not defined. This could lead to build failures because of missing tools
that could have been detected already at parsing time. In practice there is no
need to rely on this behavior. It is always possible to define a place holder
recipe to syntactically satisfy the dependency.

Old behavior
   It is not necessary that tools are actually defined when being used in a
   recipe. If they are available they will be used. If a tool is undefined it
   is silently ignored.

New behavior
   Tools listed in  ``{checkout,build,package}Tools`` must be defined. Any
   undefined tool will lead to a parsing error.

.. _policies-scmIgnoreUser:

scmIgnoreUser
~~~~~~~~~~~~~

Introduced in: 0.18

The user information part of an URL is used as authentication for the resource
that is encoded in the rest of the URL. Except for gaining authorization to the
resource, the user information fundamentally does not influence the content
that is referenced by the URL. To share binary artifacts between different user
identities and to prevent repeated checkouts Bob will ignore the user
information. This policy affects the ``git`` and ``url`` SCMs.

Old behavior
   The user information of the URL is significant for the checkout content.
   Binary artifacts are not shared between different users. If the user
   information of an URL changes the checkout is moved to the attic.

New behavior
   The user information in the URL of ``git`` and ``url`` SCMs is ignored. Bob
   assumes that the actual content is unaffected by the authentication part.

.. _policies-pruneImportScm:

pruneImportScm
~~~~~~~~~~~~~~

Introduced in: 0.18

The import SCM syncs a directory from the recipes to the source workspace.
Before Bob 0.18 this was not done when building with ``--build-only`` even
though the files are already locally present. It was anticipated that the user
instead edits the source workspace directly and syncs its changes back to the
recipes. To make this workable the ``prune`` property defaulted to ``False`` to
prevent accidental deletion of changed in the workspace.

This proved to be confusing, inefficient and additionally had the problem to
potentially leave stale files in the workspace. Starting with Bob 0.18 the
import SCM is always updated even if ``--build-only`` is specified. Now the
user never needs to edit the workspace and the ``prune`` policy is mostly
useless. This policy changes the default but keeps the property so that a user
is still able to retain the old behaviour on a case-by-case basis.

Old behaviour
   The ``prune`` property of the import SCM defaults to ``False``. Deletions of
   files at the source location are not propagated to the workspace. Files are
   only overwritten if the source is younger than the destination file in the
   workspace. This may lead to wrong build results because of stale files.

New behaviour
   The ``prune`` property defaults to ``True``. The user must edit the files at
   the import source location because the destination in the workspace is
   overwritten and obsolete files are deleted.

.. _policies-gitBranchAndCommit:

gitCommitOnBranch
~~~~~~~~~~~~~~~~~~

Introduced in: 0.22

This policy handles the use of git if ``commit`` and/or ``tag``  and ``branch``
are named in the recipe. Before Bob 0.22 the commit took precedence and the branch
was ignored. The commit was checked out leaving the repo in a detached HEAD state.
For the developer this makes some additional steps necessary, e.g. switching to
a branch before being able to push. If the ``commit`` was not on the ``branch``
special attention must be paid. Otherwise a commit might got lost.

Old behavior
   ``commit`` was checked out leaving the repo in a detached HEAD state.

New behavior
   Bob checks if the ``commit`` and / or ``tag`` is on the configured ``branch`` and
   performs a checkout of the ``commit`` on a local ``branch``.

.. _policies-fixImportScmVariant:

fixImportScmVariant
~~~~~~~~~~~~~~~~~~~

Introduced in: 0.23

Bob uses the concept of a :term:`Variant-Id` to track *how* a package is built.
This includes the sub-directory in which a particular SCM is checked out. So if
the ``dir`` attribute of an SCM changes, the respective Variant-Id of the
package changes too. Bob versions before 0.23 contained a bug where the ``dir``
attribute of an ``import`` SCM was not included in the Variant-Id calculation.
This can cause build failures or wrongly used binary artifacts if just the
``dir`` attribute of an ``import`` SCM is changed.

Fixing the bug will affect the :term:`Variant-Id` of all packages that use an
``import`` SCM. This implies that binary artifacts of such packages will need
to be built again. It also transitively affects packages that depend on
packages that utilize an ``import`` SCM.

Old behavior
   Changes to the ``dir`` attribute of an ``import`` SCM do not cause rebuilds
   of the affected package. Wrong sharing of binary artifacts for such packages
   may occur.

New behavior
   Changes to the ``dir`` attribute of an ``import`` SCM behave the same as for
   any other SCM.
