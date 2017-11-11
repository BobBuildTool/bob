.. _manpage-bob-status:

bob-status
==========

.. only:: not man

   Name
   ----

   bob-status - Show SCM status

Synopsis
--------

::

    bob status [-h] [--develop | --release] [-r] [-D DEFINES]
               [-c CONFIGFILE] [-e NAME] [-E] [-v] [--show-overrides]
               packages [packages ...]

Description
-----------

Show SCM status

Options
-------

``-c CONFIGFILE``
    Use config File

``-D DEFINES``
    Override default environment variable

``--develop``
    Use developer mode. This is default.

``-e NAME``
    Preserve environment variable

``-E``
    Preserve whole environment

``-r, --recursive``
    Recursively display dependencies

``--release``
    Use release mode

``--show-overrides``
    Print informations about active scmOverrides.

``-v, --verbose``
    Increase verbosity (may be specified multiple times)

Output
------

The output of bob status is at least one line per package. Only if the status is
unclean or if ``--verbose`` is given a additional line is printed. A status line
consists of one ore more status codes followed by the scm path.

Status codes can be interpreted as follows:

    - ``M`` = modified. Some sources have been modified and not yet committed to SCM.
    - ``S`` = switched. The commit/tag/branch/url is different from the recipe.
    - ``U`` = unpushed. Git only. Some commits are made locally and not yet pushed to the SCM.
    - ``u`` = unpushed on a local branch. Git only. A local branch exists with unpushed commits. This branch differs from the branch specified in the recipe.
    - ``O`` = overridden. This SCM is overridden by an scmOverride. This information is only shown if ``--show-overrides`` is passed.

