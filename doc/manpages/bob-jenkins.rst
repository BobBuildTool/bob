bob-jenkins
===========

.. only:: not man

   Name
   ----

   bob-jenkins - Configure Jenkins server

Synopsis
--------

Generic command format:

::

    bob jenkins [-h] [-c NAME] subcommand ...

Available sub-commands:

::

    bob jenkins add [-h] [-n NODES] [-o OPTIONS] [-w] [-p PREFIX] [-r ROOT]
                    [-D DEFINES] [--keep] [--download] [--upload]
                    [--no-sandbox] [--credentials CREDENTIALS] [--clean]
                    name url
    bob jenkins export [-h] name dir
    bob jenkins graph [-h] name
    bob jenkins ls [-h] [-v]
    bob jenkins prune [-h] [--obsolete | --intermediate] [-q] [-v] name
    bob jenkins push [-h] [-f] [--no-trigger] [-q] [-v] name
    bob jenkins rm [-h] [-f] name
    bob jenkins set-options [-h] [--reset] [-n NODES] [-o OPTIONS] [-p PREFIX]
                            [--add-root ADD_ROOT] [--del-root DEL_ROOT]
                            [-D DEFINES] [-U UNDEFINES] [--credentials CREDENTIALS]
                            [--keep | --no-keep] [--download | --no-download]
                            [--upload | --no-upload] [--sandbox | --no-sandbox]
                            [--clean | --incremental] [--autotoken AUTHTOKEN]
                            name
    bob jenkins set-url [-h] name url


Description
-----------

Options
-------

``--add-root ADD_ROOT``
    Add new root package

``--authtoken AUTHTOKEN``
    Add a authentication token to trigger job remotely

``--clean``
    Do clean builds (clear workspace)

``--credentials CREDENTIALS``
    Credentials UUID for SCM checkouts

``-D DEFINES``
    Override default environment variable

``--del-root DEL_ROOT``
    Remove existing root package

``--download``
    Download from binary archive

``-f, --force``
    Overwrite existing jobs

``--incremental``
    Reuse workspace for incremental builds

``--intermediate``
    Delete everything except root jobs

``--keep``
    Keep obsolete jobs by disabling them

``-n NODES, --nodes NODES``
    Label for Jenkins Slave

``--no-download``
    Disable binary archive download

``--no-keep``
    Delete obsolete jobs

``--no-sandbox``
    Disable sandboxing

``--no-trigger``
    Do not trigger build for updated jobs

``--no-upload``
    Disable binary archive upload

``-o OPTIONS``
    Set extended Jenkins options. This option expects a ``key=value`` pair to
    set one particular extended configuration parameter. May be specified
    multiple times. See :ref:`bob-jenkins-extended-options` for the list of
    available options. Setting an empty value deletes the option.

``--obsolete``
    Delete obsolete jobs that are currently not needed according to the
    recipes.

``-p PREFIX, --prefix PREFIX``
    Prefix for jobs

``-q, --quiet``
    Decrease verbosity (may be specified multiple times)

``-r ROOT, --root ROOT``
    Root package (may be specified multiple times)

``--reset``
    Reset all options to their default

``--sandbox``
    Enable sandboxing

``-U UNDEFINES``
    Undefine environment variable override

``--upload``
    Upload to binary archive

``-v, --verbose``
    Show additional information

``-w, --windows``
    Jenkins is running on Windows. Produce cygwin compatible scripts.

Commands
--------

prune
    Prune jobs from Jenkins server.

    By default all jobs managed by the Jenkins alias will be deleted. If the
    'keep' option is enabled for this alias you may use the '--obsolete' option
    to delete only currently disabled (obsolete) jobs. Alternatively you may
    delete all intermediate jobs and keep only the root jobs by using
    '--intermediate'. This will disable the root jobs because they cannot run
    anyawy without failing.

.. _bob-jenkins-extended-options:

Extended Options
----------------

The following Jenkins plugin options are available. Any unrecognized options
are ignored.

scm.git.shallow
    Instruct the Jenkins git plugin to create shallow clones with a history
    truncated to the specified number of commits. If the parameter is unset
    or "0" the full history will be cloned.

    .. warning::
       Setting this parameter too small may prevent the creation of a proper
       change log. Jenkins will not be able to find the reference commit of
       the last run if the branch advanced by more commits than were cloned.

scm.poll
    Without this option the Jenkins server is dependent on external commit
    hooks to be notified of changes in the source code repositories. While this
    is the preferred solution it might be necessary to fall back to polling in
    some setups. Set this option to a Jenkins flavoured cron line, e.g.
    ``H/15 * * * *``.

shared.dir
    Any packages that are marked as :ref:`shared <configuration-recipes-shared>`
    (``shared: True``) are installed upon usage on a Jenkins slave in a shared
    location. By default this is ``${JENKINS_HOME}/bob``. To use another
    directory set this option to an absolute path. If you expand Jenkins
    environment variables make sure that they follow the syntax of the default
    value because the path is also expanded by the Token Macro plugin.

