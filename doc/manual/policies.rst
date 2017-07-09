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
    relative to the current recipes directory. Global configuration files use
    the new policy in any case.

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
