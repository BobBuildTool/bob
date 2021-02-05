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
                    [--shortdescription] [--longdescription]
                    name url
    bob jenkins export [-h] name dir
    bob jenkins graph [-h] name
    bob jenkins ls [-h] [-v]
    bob jenkins prune [-h] [--obsolete | --intermediate] [--no-ssl-verify]
                      [-q] [-v]
                      name
    bob jenkins push [-h] [-f] [--no-ssl-verify] [--no-trigger] [-q] [-v]
                     name
    bob jenkins rm [-h] [-f] name
    bob jenkins set-options [-h] [--reset] [-n NODES] [-o OPTIONS] [-p PREFIX]
                            [--add-root ADD_ROOT] [--del-root DEL_ROOT]
                            [-D DEFINES] [-U UNDEFINES] [--credentials CREDENTIALS]
                            [--keep | --no-keep] [--download | --no-download]
                            [--upload | --no-upload] [--sandbox | --no-sandbox]
                            [--clean | --incremental] [--autotoken AUTHTOKEN]
                            [--shortdescription]
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

``--longdescription``
    Every path to a package will be calculated and displayed in job description

``-n NODES, --nodes NODES``
    Label for Jenkins slave. If empty, the jobs can be scheduled on any slave.

``--no-download``
    Disable binary archive download

``--no-keep``
    Delete obsolete jobs

``--no-sandbox``
    Disable sandboxing

``--no-ssl-verify``
    Disable HTTPS certificate checking

    By default only secure connections are allowed to HTTPS Jenkins servers. If
    this option is given then any certificate error is ignored. This was the
    default before Bob 0.15.

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

``--shortdescription``
    Do not calculate every path for every variant.
    Leads to short job description: One path for each variant.

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

artifacts.copy
    This options selects the way of sharing archives between workspaces.
    Possible values are:

    jenkins
         Use copy artifacts pluing to copy result and buildId to jenkins-master.
         The downstream job will afterwards be configured to use copy artifact
         plugin again and copy the artifact into it's workspace. This is the
         default.

    archive
         Only copy the buildID file to to jenkins master and use the binary
         archive for sharing artifacts. Must be used together with ``--upload``
         and ``--download``.

audit.meta.<var>
   Assign the meta variable ``<var>`` to the given value in the audit trail.
   The variable can later be matched by :ref:`bob archive <manpage-archive>` as
   ``meta.<var>`` to select artifacts built by this project. Variables that are
   defined by Bob itself (e.g. ``meta.jenkins-node``) cannot be redifined!

jobs.gc.deps.artifacts
   The number of build artifacts that are retained of intermediate or leaf
   jobs. Only useful for ``artifacts.copy=jenkins``. Protocols and build logs
   are not affected and will still be kept. Defaults to ``1``. If set to 0 all
   artifacts will be retained.

jobs.gc.deps.builds
   Configure the number of builds that are retained of intermediate and leaf
   jobs. Logs and artifacts of old builds exceeding this threshold are deleted
   automatically by Jenkins. A separate binary archive
   (``artifacts.copy=archive``) is not affected and must be separately managed
   with :ref:`bob archive <manpage-archive>`. If not set, all Jenkins builds
   will be kept.

jobs.gc.root.artifacts
   The number of build artifacts that are retained of root-jobs. Only useful
   for ``artifacts.copy=jenkins``. Protocols and build logs are not affected
   and will still be kept. By default everything will be retained.

jobs.gc.root.builds
   Configure the number of builds that are retained of root-jobs. These are
   jobs that build packages that were given by the ``-r`` option. Logs and
   artifacts of old builds exceeding this threshold are deleted automatically
   by Jenkins. A separate binary archive (``artifacts.copy=archive``) is not
   affected but must be separately managed with :ref:`bob archive
   <manpage-archive>`.  If not set, all Jenkins builds will be kept.

jobs.isolate
    Regular expression that is matching package names. Any package that is
    matched is put into a separate job. Multiple variants of the same package
    are still kept in the same job, though.

    This option might be used to single out specific packages into dedicated
    Jenkins jobs that are unrelated to other jobs in the recipe. Typical use
    cases are documentation and testing ``multiPackage`` that should not
    prevent other packages from building if they fail. The obvious draw back is
    that common checkout and build steps might be duplicated to multiple jobs,
    though.

jobs.policy
    Controls how downstream jobs are triggered and which artifacts of the
    upstream jobs are used. By default only stable jobs trigger further
    downstream builds. The following settings are available:

    stable
        Downstream jobs are triggered only if the build was stable. Likewise,
        only the artifacts of stable upstream builds are used. This is the
        default.

    unstable
        Downstream jobs are triggered on successful builds, that is stable and
        unstable builds. The downstream jobs will also use the last build that
        succeeded, even if that build was unstable.

    always
        Downstream jobs are triggered regardless of the build result, even on
        failed builds. The artifacts are taken from the last completed build of
        the upstream job which might not necessarily have published one because
        it failed before archiving them.

jobs.update
    Whenever the recipes are changed Bob has to update the individual Jenkins
    jobs that are affected by the change. This switch controls how the
    description and audit trail information is updated if only these are
    affected by the change. Their update may be deferred unless strictly
    necessary and still generate a correct build result at the expense of the
    freshness of this information.

    always
        Always update the description and audit trail information if they
        change. This is the default. Note that ``bob jenkins push`` will always
        update the description because the date and time of the update is part
        of the job description.

    description
        Keep the description up-to-date but defer audit trail updates unless
        strictly necessary. This may provide marginal speed gains but will
        still update all jobs because the description contains the recipe
        version and update time.

    lazy
        Only update a job if it will build a different artifact than before.
        The description and audit trail information will be left unchanged
        otherwise. This will provide considerable speed improvements at the
        expense of an outdated description of the unchanged jobs.

scm.git.shallow
    Instruct the Jenkins git plugin to create shallow clones with a history
    truncated to the specified number of commits. If the parameter is unset
    or "0" the full history will be cloned.

    .. warning::
       Setting this parameter too small may prevent the creation of a proper
       change log. Jenkins will not be able to find the reference commit of
       the last run if the branch advanced by more commits than were cloned.

scm.git.timeout
    Instruct the Jenkins git plugin to use the given timeout (minutes) for clone 
    and fetch operations.

scm.ignore-hooks
    Boolean option (possible values: '0' or 'false' resp. '1' or 'true') to set
    the "Ignore post-commit hooks" option on all jobs. This instructs Jenkins
    to ignore changes notified by SCM post-commit hooks if enabled. You should
    probably set a sensible polling interval with the ``scm.poll`` option
    unless you want to trigger the generated jobs manually.

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


