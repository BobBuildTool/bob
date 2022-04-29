.. _manpage-bob-jenkins:

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

    bob jenkins add [-h] [-n NODES] [-o OPTIONS]
                    [--host-platform {linux,msys,win32}] [-w] [-p PREFIX]
                    [-r ROOT] [-D DEFINES] [--keep] [--download] [--upload]
                    [--no-sandbox] [--credentials CREDENTIALS] [--clean]
                    [--shortdescription | --longdescription]
                    name url
    bob jenkins export [-h] name dir
    bob jenkins graph [-h] name
    bob jenkins ls [-h] [-v]
    bob jenkins prune [-h] [--obsolete | --intermediate] [--no-ssl-verify]
                      [--user USER] [--password PASSWORD] [-q] [-v]
                      name
    bob jenkins push [-h] [-f] [--no-ssl-verify] [--no-trigger]
                     [--user USER] [--password PASSWORD] [-q] [-v]
                     name
    bob jenkins rm [-h] [-f] name
    bob jenkins set-options [-h] [--reset] [-n NODES] [-o OPTIONS]
                            [--host-platform {linux,msys,win32}]
                            [-p PREFIX] [--add-root ADD_ROOT]
                            [--del-root DEL_ROOT] [-D DEFINES]
                            [-U UNDEFINES] [--credentials CREDENTIALS]
                            [--authtoken AUTHTOKEN]
                            [--shortdescription | --longdescription]
                            [--keep | --no-keep]
                            [--download | --no-download]
                            [--upload | --no-upload]
                            [--sandbox | --no-sandbox]
                            [--clean | --incremental]
                            name
    bob jenkins set-url [-h] name url


Description
-----------

The ``bob jenkins`` command can be used to build a project on a Jenkins server
remotely. Bob will create the necessary Jenkins jobs on the server, configure
them and queue their build. If recipes or configuration options are updated,
the Jenkins jobs will be updated incrementally as needed.

For the most common usage two steps are necessary: add a Jenkins configuration
under an alias name and push this configuration to the server. A Jenkins
configuration is created by ``bob jenkins add`` which can later be updated by
``bob jenkins set-options`` or ``set-url``. To push the current configuration
to the server use ``bob jenkins push``. As a rule of thumb Bob will create one
job for each built recipe.

For more details and an overview about the required Jenkins configuration,
please read the :ref:`Jenkins Tutorial <tut-jenkins>`.

.. _manpage-bob-jenkins-options:

Options
-------

``--add-root ADD_ROOT``
    Add new root package. See the ``--root`` option below for its detailed
    description.

``--authtoken AUTHTOKEN``
    Add an authentication token to restrict remote build triggers.

    A job build can be scheduled by sending a HTTP POST request to
    ``$JENKINS_URL/job/$JOB/build``. To allow unauthenticated scripts to be
    able to trigger builds too, a secret authentication token can be added to
    all jobs and the build can be started by POSTing to
    ``$JENKINS_URL/job/$JOB/build?token=AUTHTOKEN``. Note that you have to keep
    the token secret because anyone who has the right to view the job
    configuration will be able to trigger a build.

``--clean``
    Do clean builds.

    Whenever a Jenkins job is triggered, the workspace is erased first. This
    will cause a fresh checkout of the sources and a clean (re)build of all
    packages in the job.

    Use this option to ensure reproducibility at the expense of speed.
    Disabled by default. See ``--incremental`` for the opposite switch.

``--credentials CREDENTIALS``
    Credentials ID for git/svn SCM checkouts.

    By default SCM checkouts of Git and Subversion modules are done
    anonymously. This might be insufficient if the Jenkins build nodes lack
    the proper credentials to access the repositories. Jenkins has a built-in
    credentials store ("Manage Jenkins » Credentials") where passwords and
    SSH keys can be managed. Add the required credentials there and use the
    "ID" field of the required entry here as ``CREDENTIALS`` parameter. Usually
    the ID will be a UUID but Jenkins allows any unique identifier to be used.

    All jobs of an alias will be configured with the same credentials. If a
    more fine grained credentials configurations is required, a plugin must be
    used. See the :ref:`"Jenkins job mangling" <extending-plugins-jenkins>`
    section in the manual for more information.

``-D DEFINES``
    Override default environment variable.

``--del-root DEL_ROOT``
    Remove existing root package.

``--download``
    Enable downloads from binary archive. Disabled by default. There must
    be at least one binary archive in the user configuration
    :ref:`archive <configuration-config-archive>` section that is enabled
    for Jenkins builds.

``-f, --force``
    Force the operation, potentially with loss of information. The exact
    semantics depend on the sub-command where the switch is used:

    ``push``
        Overwrite existing jobs.

        By default, Bob will refuse to overwrite jobs that were not created by
        himself. If you are sure that the existing jobs are safe to be
        overwritten, you can use this switch. Otherwise the jobs must be either
        deleted manually or by the ``prune`` command of the project that
        created the them in the first place. Additionally all job
        configurations are written, even if they have not changed. This
        overwrites any possible manual changes made to the jobs.

    ``rm``
        Remove the Jenkins alias, even if there are active jobs. You will have
        to delete the jobs manually.

``--host-platform``
    Jenkins host platform type. May be any of ``linux``, ``msys`` or ``win32``.

    This specifies the host operating system where the Jenkins master and the
    build slaves are running. By default this is the type of the current
    operating system.

``--incremental``
    Reuse workspace for incremental builds. This is the default.

    Bob will still apply the internal heuristics to make clean builds where
    recipes or any of the dependencies were changed. Use ``--clean`` to always
    force clean builds of packages.

``--intermediate``
    Delete everything except root jobs.

    Use this switch if you want to delete a project from the Jenkins server
    but want to keep the jobs with the final artifacts. The root jobs will
    be disabled because their dependencies are deleted. You can push an alias
    again to re-create all jobs and re-enable the root jobs.

``--keep``
    Keep obsolete jobs by disabling them instead of deleting.

    If the recipes or configuration of a project is changed, some of the
    previously required packages could become unnecessary. By default Bob will
    delete the corresponding jobs. By using the ``--keep`` switch these jobs
    will merely be disabled. This retains the build logs and artifacts.

    You can use ``bob jenkins prune --obsolete`` to delete disabled jobs
    manually. See ``--no-keep`` for the inverse option.

``--longdescription``
    Display all paths of all packages in the job description.

    Note that the number of displayed package paths of (content wise) identical
    packages is still limited. Nonetheless it is computationally expensive to
    calculate every possible package path in the first place. Except for
    trivial projects this might cause a noticeable delays in the Jenkins
    configuration. See ``--shortdescription`` on how to disable this behaviour.

``-n NODES, --nodes NODES``
    Label expression for Jenkins slave. If empty, the jobs can be scheduled on
    any slave.

    In the Jenkins configuration every build node can be assigned one or more
    label. The expression given in ``NODES`` restricts on which build nodes
    the jobs can be scheduled. It can either be a single label or a boolean
    expression of labels. The "built-in" label is pre-defined and identifies
    the Jenkins master. Expressions can use parentheses "(expression)", negation "!",
    logical AND "&&" and logical OR "||".

    Examples:

    * ``win32``
    * ``linux && 64bit``
    * ``!win32 || (vm && mysql)``

``--no-download``
    Disable binary archive downloads. This is the default. See ``--download``
    for the enabling counterpart.

``--no-keep``
    Delete obsolete jobs. This is the default.

    Jobs that are not required any more will be deleted. Use ``--keep`` if
    you instead want to just disable such jobs.

``--no-sandbox``
    Disable sandboxing during builds.

    Unless required by the project, it is discouraged to disable the sandbox
    feature. See ``--sandbox`` for the opposite switch.

``--no-ssl-verify``
    Disable HTTPS certificate checking.

    By default only secure connections are allowed to HTTPS Jenkins servers. If
    this option is given then any certificate error is ignored. This was the
    default before Bob 0.15.

``--no-trigger``
    Do not trigger build for updated jobs.

    You have to manually schedule the build of all changed jobs. Triggering
    only a subset of the affected jobs can lead to build errors because of
    unbuilt dependencies. Use with caution.

``--no-upload``
    Disable binary archive uploads. This is the default. See ``--upload``
    for the enabling counterpart.

``-o OPTIONS``
    Set extended Jenkins options. This option expects a ``key=value`` pair to
    set one particular extended configuration parameter. May be specified
    multiple times. See :ref:`bob-jenkins-extended-options` for the list of
    available options. Setting an empty value deletes the option.

``--obsolete``
    Delete obsolete jobs that are currently not needed according to the
    recipes. Use this switch with the ``prune`` command to delete jobs that
    are left disabled due to ``--keep`` being active.

``-p PREFIX, --prefix PREFIX``
    Prefix for job names.

    By default the job names are derived from the recipe and package names. If
    you want to build the same project with different configurations on the
    same server you will have to use unique prefixes for each. Otherwise the
    jobs names will collide and configuration will fail.

``--password``
    Set password for Jenkins authentication.

    You can also set the user name and password persistently by encoding it
    into the Jenkins url directly, e.g. *https:://user:password@host/*.

    .. attention::
       On Linux users can usually see the program arguments of processes from
       other users. By using the ``--password`` you could inadvertently reveal
       the password to untrusted other users that have access to the same
       machine.  It is safer to either enter the password manually or to pipe
       it through stdin.

``-q, --quiet``
    Decrease verbosity (may be specified multiple times).

``-r ROOT, --root ROOT``
    Root package to build (may be specified multiple times).

    Specify the root packages that are built. All dependencies are added
    implicitly. Jobs building the root packages are treated a bit differently
    in that their logs and artifacts will be retained indefinitely by default.
    See the ``jobs.gc.*`` extended options on how to tweak this behavior.

``--reset``
    Reset all options to their default.

    Use this option to revert all configuration options back to their default
    state. This option is applied before all other options of the
    ``set-options`` sub-command. Use it to configure an alias without relying
    on the previous state.

``--sandbox``
    Enable sandboxing. This is the default.

``--shortdescription``
    Do not calculate every possible path of each package in a job for the
    description. This leads to shorter job descriptions and, depending on the
    project complexity, might reduce the configuration time considerably. The
    drawback is that not all packages are then listed in the job description.
    For each unique package only one example path will be shown.

``-U UNDEFINES``
    Undefine environment variable override. This removes a variable previously
    defined with ``-D``.

``--upload``
    Upload to binary archives. Disabled by default. There must
    be at least one binary archive in the user configuration
    :ref:`archive <configuration-config-archive>` section that is enabled
    for Jenkins builds.

    If the upload fails the respective job will fail too, unless the ``nofail``
    option was set on the archive entry in the configuration.

``--user``
    Set user name for Jenkins authentication.

    You can also set the user name persistently by encoding it into the Jenkins
    url directly, e.g. *https:://user@host/*.

``-v, --verbose``
    Show additional information. Can be given multiple times to further
    increase the output verbosity.

``-w, --windows``
    Jenkins is running on Windows with an MSYS2 environment. This option has
    been deprecated in favour of ``--host-platform msys`` switch.

Commands
--------

add
    Add an alias for a Jenkins configuration.

    The alias will hold the URL of the Jenkins, the desired configuration (e.g.
    what packages should be built) and the state of the last uploaded
    configuration. The state will be stored in the current project workspace.
    Any number of aliases can be added.

    Adding an alias is the first step required to build a project on a Jenkins
    server. The configuration for this alias can be later updated by the
    ``set-options`` and ``set-url`` commands. To remove an alias use the ``rm``
    command.

export
    Write the Jenkins configuration of an alias to a directory.

    For each job, the generated config.xml file will be created in the output
    directory. This is mainly a debugging aid and can be used to inspect the
    generated configuration. It is *not* intended to upload these configuration
    files to a Jenkins server. Use ``push`` for that.

graph
    Generate a Graphviz dot graph.

    Feed the generated graph through the ``dot`` tool to get a visualization
    about the jobs and their dependencies.

ls
    List all configured Jenkins aliases and their configuration.

    Without any further options, only the list of Jenkins aliases is shown. By
    adding the ``-v`` option the configuration of each alias is displayed too.
    A 2nd ``-v`` will additionally show all currently configured jobs.

prune
    Prune jobs from Jenkins server.

    By default all jobs managed by the Jenkins alias will be deleted. If the
    ``--keep`` option is enabled for this alias, you may use the ``--obsolete``
    option to delete only currently disabled (obsolete) jobs. Alternatively you
    may delete all intermediate jobs and keep only the root jobs by using
    ``--intermediate``. This will disable the root jobs because they cannot run
    anyway without failing.

push
    Push current configuration of an alias to the Jenkins server.

    This will create or update all necessary jobs and schedule their build. By
    default obsolete jobs will be deleted unless the ``--keep`` option has been
    enabled. If you just want to create or update the jobs without scheduling
    their build, use the ``--no-trigger`` option. Bob won't overwrite jobs that
    were not created by Bob for the Jenkins alias itself unless the ``-f``
    option is given.

    Existing jobs will be updated as necessary. In the default configuration
    this happens always because the job description displays the state of the
    recipes and the time of the ``bob jenkins push`` operation. Use one of the
    other modes of the ``jobs.update`` extended option to speed up the push
    operation at the expense of slightly outdated job descriptions.

rm
    Remove Jenkins alias.

    The alias will not be removed if jobs are still existing. It is thus
    usually required to run the ``prune`` command before to delete all jobs of
    an alias. Alternatively the ``-f`` switch may be used to remove the alias
    even though the state indicates that there are still existing jobs. This is
    useful e.g. if the Jenkins server is not running any more or the jobs have
    already been deleted externally.

set-options
    Change configuration of an alias.

    Can update all options of an alias except the server URL. The new
    configuration can then be synchronized to the Jenkins server by a
    subsequent ``push`` command. To revert the whole configuration to its
    default state use ``--reset``. This is done as the first step so that you
    can combine ``--reset`` with all other options to fully control all
    options.

set-url
    Update server URL of an alias.

.. _bob-jenkins-extended-options:

Extended Options
----------------

The following extended Jenkins options are available. Any unrecognized options
will be rejected.

artifacts.copy
    This options selects the way of sharing archives between workspaces.
    Possible values are:

    jenkins
         Store the result and :term:`Build-Id` of the job on the Jenkins master.
         Subsequently the downstream job will be configured to use the copy
         artifact plugin to copy the artifact into it's workspace. This is the
         default.

    archive
         Only store the :term:`Build-Id` on the Jenkins master and use a
         separate binary archive for sharing artifacts. Must be used together
         with ``--upload`` and ``--download``.

audit.meta.<var>
   Assign the meta variable ``<var>`` to the given value in the audit trail.
   The variable can later be matched by :ref:`bob archive <manpage-archive>` as
   ``meta.<var>`` to select artifacts built by this project. Variables that are
   defined by Bob itself (e.g. ``meta.jenkins-node``) cannot be redefined!

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
    some setups. Set this option to a `Jenkins flavoured cron line
    <https://www.jenkins.io/doc/book/pipeline/syntax/#cron-syntax>`_, e.g.
    ``H/15 * * * *``.

shared.dir
    Any packages that are marked as :ref:`shared <configuration-recipes-shared>`
    (``shared: True``) are installed upon usage on a Jenkins slave in a shared
    location. By default this is ``${JENKINS_HOME}/bob``. To use another
    directory set this option to an absolute path.

    .. attention::
      The string is subject to :ref:`string substitution
      <configuration-principle-subst>`.  It is possbile to substitute
      envirionment variables that are set in the Jenkins execution environment.
      Make sure that any meta characters are properly escaped. Because
      backslash is such a character, special care must be taken on Windows. It
      is best to always use forward slashes, even on Windows, to evade any
      escaping issues.

shared.quota
    Set a limit to the amount of disk space that is used for the shared
    location on each build node. By default there is no limit. The size is
    given in bytes with optional magnitude suffix. The standard IEC units are
    supported (``KiB``, ``MiB``, ``GiB`` and ``TiB``) which can optionally be
    abbreviated by leaving out the ``iB`` suffix (e.g. ``G`` for ``GiB``). SI
    units (base 1000) are supported too (``KB``,  ``MB``, ``GB``, and ``TB``).

    .. note::
       Only unused packages will be deleted when the quota is reached. If there
       are no unused shared packages, e.g. because the workspaces of obsolte
       jobs were not deleted, it is still possible that the disk usage is above
       the quota.
