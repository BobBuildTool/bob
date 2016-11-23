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

    bob jenkins add [-h] [-n NODES] [-w] [-p PREFIX] [-r ROOT] [-D DEFINES]
                    [--keep] [--download] [--upload] [--no-sandbox]
                    [--credentials CREDENTIALS] [--clean]
                    name url
    bob jenkins export [-h] name dir
    bob jenkins graph [-h] name
    bob jenkins ls [-h] [-v]
    bob jenkins prune [-h] [--obsolete | --intermediate] name
    bob jenkins push [-h] [-f] [--no-trigger] [-q] [-v] name
    bob jenkins rm [-h] [-f] name
    bob jenkins set-options [-h] [--reset] [-n NODES] [-p PREFIX]
                            [--add-root ADD_ROOT] [--del-root DEL_ROOT]
                            [-D DEFINES] [-U UNDEFINES]
                            [--credentials CREDENTIALS]
                            [--keep | --no-keep]
                            [--download | --no-download]
                            [--upload | --no-upload]
                            [--sandbox | --no-sandbox]
                            [--clean | --incremental]
                            name
    bob jenkins set-url [-h] name url


Description
-----------

Options
-------

``--add-root ADD_ROOT``
    Add new root package

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

``--obsolete``
    Delete only obsolete jobs

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

