.. highlight:: bash

.. _tut-jenkins:

Using Jenkins to build Bob projects
***********************************

Bob natively supports Jenkins to build projects. For that, Bob is capable of
creating and updating Jenkins jobs that build the various packages of a
project. Apart from Bob itself on build nodes and a couple of plugins, no
special requirements are made on the Jenkins setup.

The Jenkins tutorial will use *bob-tutorials* repository as basis for example
projects. As the first step, clone the tutorial projects::

    $ git clone https://github.com/BobBuildTool/bob-tutorials.git
    $ cd bob-tutorials

Prerequisites
=============

Jenkins installation
--------------------

The generic installation of Jenkins is described in detail in the `User
Handbook <https://www.jenkins.io/doc/book/installing/>`_.

.. attention::
   The default installation on Windows will put the Jenkins home directory
   inside ``%LocalAppData%\Jenkins\.jenkins``. This path is usually quite long
   already and you might hit the Windows path length limitiations when building
   projects. It is thus strongly recommended to change the ``JENKINS_HOME``
   environment variable in ``jenkins.xml`` (located where you installed Jenkins
   in the first place) to a sensible short path.

After the basic installation is done, a couple of plugins must be installed.
The following plugins need to be available on the Jenkins server so that Bob
created jobs can run successfully:

* `Conditional BuildStep Plugin`_: used to efficiently support shared packages
* `Copy Artifact plugin`_: used to carry results between the different jobs
* `Git plugin`_: to clone git repositories
* `Multiple SCMs plugin`_: used to support recipes that have multiple checkouts
* `Subversion plugin`_: to checkout SVN modules
* `Workspace Cleanup Plugin`_: to make clean builds if requested

.. _Copy Artifact plugin: https://plugins.jenkins.io/copyartifact/
.. _Subversion plugin: https://plugins.jenkins.io/subversion/
.. _Git plugin: https://plugins.jenkins.io/git/
.. _Multiple SCMs plugin: https://plugins.jenkins.io/multiple-scms/
.. _Conditional BuildStep Plugin: https://plugins.jenkins.io/conditional-buildstep/
.. _Workspace Cleanup Plugin: https://plugins.jenkins.io/ws-cleanup/

If possible, Bob will check that these plugins are actually installed. If the
credentials available to Bob do not allow to query the plugins, it will not be
possible to verify the availability of the plugins automatically, though. This is
not treated as an error.

You might also consider installing the `OWASP Markup Formatter
<https://plugins.jenkins.io/antisamy-markup-formatter/>`_ plugin. Bob will
create job descriptions that use HTML. To view them properly, make sure this
plugin is installed and select "Safe HTML" in "Manage Jenkins » Configure
Global Security » Markup Formatter".

Setting up PATH
---------------

Regardless of the platform, Bob must be installed on all Jenkins build nodes
and be available in the PATH. It is required to install the same version of Bob
on the build nodes and the computer that is used to configure the Jenkins.
Otherwise the build will fail.

Windows
~~~~~~~

When using native Windows, make sure you followed the
:ref:`installation-windows` and installed Bob globally for all users. Otherwise
your Jenkins server won't be able to execute Bob in its jobs which is required
to build projects! Of course it is also possible to install Python and Bob only
for the user that runs Jenkins.

If your recipes use Bash you must additionally install `MSYS2`_ and add the
path to ``bash.exe`` *after* the native Python interpreter. Otherwise the MSYS2
Python interpreter might be invoked which does not work.

MSYS2
~~~~~

In contrast to a native Windows usage it is required that the build agent
is run in an MSYS2 environment. This implies to either run the ``agent.jar``
from an MSYS2 shell or to setup the environment variables accordingly. See
`this blog post <https://blog.insane.engineer/post/jenkins_msys2/>`_ for
more information.

Subversion project
------------------

Bob natively supports Subversion as source control management system. On Jenkins
the respective `Subversion plugin`_ will be used automatically. The plugin
uses a Java implementation of the subversion protocol and does *not* call the
command line ``svn`` implementation. However, during the build, Bob will
call the command line version of Subversion to gather information about the
built revision. This requires that the workspace version is compatible
or Subversion will complain that an ``upgrade`` is necessary.

.. attention::
   To make the workspace version compatible to your installed Subversion,
   adapt the Jenkins configuration in "Manage Jenkins » Configure System »
   Subversion » Subversion Workspace Version". In most cases you should use the
   latest version.  If you are using an older version of subversion you have to
   pick that one instead.

Build project
=============

Suppose you have a suitable Jenkins server located at http://localhost:8080.
Go to the project root directory and tell Bob about your server and what you
want to build there (substitute ``<user>`` and ``<pass>`` with your actual
credentials). We will use the simple *hello-world* project that consists just
of a single recipe. Depending on your platform and installed tools, you can
either choose the *bash* or *PowerShell* variants of the project. Both
effectively build the same result::

    $ cd hello-world/[bash|PowerShell]
    $ bob jenkins add local http://<user>:<pass>@localhost:8080 \
          --root hello-world

This adds an alias ("local") for your Jenkins server and its respective
project settings. You can leave out the password part (``:<pass>``) if you do
not want your password to be stored in the project. Bob will then instead ask
for the password every time it is required. The ``-r`` option (short version of
``--root``) specifies what packages should be built. It can be given any number
of times to build multiple packages by a single alias. To view the settings
type::

    $ bob jenkins ls -vv
    local
        URL: http://<user>:<pass>@localhost:8080/
        Roots: hello-world
        Obsolete jobs: delete
        Download: disabled
        Upload: disabled
        Clean builds: disabled
        Sandbox: disabled
        Jobs: 

As you can see there is no job configured yet on the server. This is done by::

    $ bob jenkins push local
    hello-world: Initial creation...
    Schedule all modified/created jobs...

which pushes the local state of the recipes as Jenkins jobs to the server. If
all required tools and plugins have been installed on Jenkins, the build should
succeed. Go into the "hello-world" job, download the archived artifacts
(named ``hello-world_....tgz``) and look at the file in ``content/result.txt``.

.. note::
   By default Bob assumes that Jenkins and its build nodes are running on the
   same operating system as where ``bob jenkins`` is executed from. If you mix
   environments, you will have to use the ``--host-platform`` option. See the
   :ref:`manpage-bob-jenkins` manpage for details.

Update project
--------------

It is very common that recipes in Bob projects are updated. When using a
Jenkins to build packages, Bob is capable of incrementally updating the existing
jobs to the new state of the recipes. Let's make a change to the
*hello-world* recipe by changing the ``"Hello World"`` string to something
else::

    $ editor recipes/hello-world.yaml

To update the Jenkins Job and build your changes just do the same ``bob jenkins
push`` again::

    $ bob jenkins push local
    hello-world: Set new configuration...
    Schedule all modified/created jobs...

This updates the job and immediately schedules it because the recipe was
changed.  After a couple of seconds the job should have been built again and
you can download the new artifact.

Tear down project
-----------------

Depending on the project size, the number of created jobs can be quite large.
Just like jobs can be created and updated by ``bob jenkins push`` it is also
possible to delete them::

    $ bob jenkins prune local
    hello-world: Delete job...

Once all jobs that were created by an alias are deleted, you can remove the
alias completely if not needed any more. Note that removing an alias is
forbidden when Bob believes the jobs have not been removed before::

    $ bob jenkins rm local

Instead of deleting all jobs of an alias, it is also possible to remove
everything *except* the root package jobs::

    $ bob jenkins prune local --intermediate

This is useful if you want to temporarily remove a project from a Jenkins
server without loosing the build history of the result packages. The root
job(s) will be disabled automatically because they cannot be built any more due
to their missing dependencies.

Managing more than one project per Jenkins server
-------------------------------------------------

On a single Jenkins server each Job must have a distinct name. To be able to
build similar or identical recipes of different projects on the same Jenkins
they can give the created jobs a unique prefix. The ``-p`` adds such a prefix::

    $ bob jenkins set-options local -p tutorial-

On the next ``bob jenkins push local`` the old job will be removed and a new one
named ``tutorial-hello-world`` will be created. By choosing unique prefixes
for each project pushed to a Jenkins you can manage many of them in parallel
without collision.

A unique prefix also enables to group all jobs of a project. This comes in
handy if each project should have its own view. When creating a view, filter
the jobs with a regular expression and match the unique prefix that you had
given the jobs: ``^tutorial-.*``.


Managing build nodes
--------------------

Depending on the project it might be necessary to build the project on
dedicated nodes. Jenkins has built in support for such use cases by giving
the build agents one or more labels. The jobs can then specify the node they
should run on by a `node expression <https://www.jenkins.io/doc/pipeline/steps/workflow-durable-task-step/#node-allocate-node>`_.

Bob supports to set the node expression for each Jenkins alias. Just set the
``--nodes`` option to the desired node expression, e.g.::

    $ bob jenkins set-options local --nodes 'linux && 64bit'

Bob cannot validate the expression but will just forward it into each created
job. Changing the node expression will update all jobs and schedule their build.
If the latter is not required you may add the ``--no-trigger`` option when
pushing the project with the updated node expression.

Continuous update through a seed job
====================================

Pushing and updating a project manually on a Jenkins might be automated if this
should be done on every recipe change. This is done by creating a *seed job* on
the Jenkins server that checks out the recipes and pushes the project onto
itself. Together with commit notifications (see `Reacting to changes in
git/svn`_ below) this can be used to fully automatically build a Bob project on
a Jenkins server.

Create dedicated user
---------------------

Because Bob needs privileged access to Jenkins to create/modify/delete and
schedule jobs, you should do this as a dedicated Jenkins user that has just the
required permissions. When using the `Matrix Authorization Strategy
<https://plugins.jenkins.io/matrix-auth/>`_ plugin you need to enable
at least the rights to *Build*, *Configure*, *Create*, *Delete*, and *Read*
in the *Element* category.

Despite having restricted rights, you should also use an API token instead of
the user name and password of the Bob account. In the event that the
credentials of the Bob user were revealed inadvertently or are leaked in any
other way it is easy to revoke the old API token and create a new one. The API
token can be created in the user configuration page reachable through the user
name drop down menu in the top right corner or by navigating to "People » *user
name* » Configure » API Token". Make sure you copy the token once you have
created it because there is no way to show it again after leaving the page.
See also the `Authenticating scripted clients`_ section of the Jenkins user
handbook for more details.

.. _Authenticating scripted clients: https://www.jenkins.io/doc/book/system-administration/authenticating-scripted-clients/

To protect the credentials, it is strongly advised to add them to the Jenkins
built in credentials store instead of using them directly in a script. See the
`respective section
<https://www.jenkins.io/doc/book/using/using-credentials/#adding-new-global-credentials>`_
in the Jenkins user handbook. The seed job can use it through the credentials
binding plugin. Check the "Use secret text(s) or file(s)" box, add a "Username
and password (separated)" entry, provide the environment variables names that
should receive the user name and password (e.g.  ``BOB_USER`` and
``BOB_TOKEN``) and choose the credentials entry that stores the dedicated users
credentials.

Seed job template
-----------------

The seed job is responsible to checkout the project and push the desired
configuration to the Jenkins server. Manually create a freestyle job, choose
and configure the SCM to fetch the recipes and add a shell build step.
Depending on the operating system, either "Execute shell" (Linux, MSYS2) or
"Execute Windows batch command" (native Windows) needs to be chosen.

Linux/MSYS2 template ("Execute shell" build step)::

    #!bash -ex

    bob --version

    if [ -z "$(bob jenkins ls)" ] ; then
      bob jenkins add local http://dummy
    fi

    bob jenkins set-url local "$JENKINS_URL"
    bob jenkins set-options local --reset \
      --add-root ...

    bob jenkins push local --user "$BOB_USER" -v <<<"$BOB_TOKEN"

Note that the token is *not* passed on the command line but rather fed into
stdin. This is done to prevent other users on the same computer to retrieve the
token. On Linux, all users are usually allowed to read the command line
arguments of processes from other users. Environment variables are safe,
though.

Windows template ("Execute Windows batch command" build step):

.. code-block:: batch

    bob --version

    bob jenkins add local http://dummy
    bob jenkins set-url local %JENKINS_URL%
    bob jenkins set-options local --reset ^
      --add-root ...

    bob jenkins push local --user %BOB_USER% --password %BOB_TOKEN% -v

.. attention::
   You should *not* run the seed jobs together with other untrusted Jenkins
   jobs on the same node. Because all jobs run as the same operating system
   user, a malicious job could otherwise retrieve the token to gain elevated
   Jenkins rights.

The main configuration part of the script are the options passed to
``bob jenkins set-options``. All options are described in the respective
:ref:`manpage <manpage-bob-jenkins-options>` section. Some options are used more
often than others, though:

* ``--download``, ``--upload``: These are usually enabled so that the binary
  archive is populated by the Jenkins centrally.
* ``--incremental``: optimize build times but flaky recipes might cause build
  problems.
* ``-p PREFIX, --prefix PREFIX``: you should almost always give the jobs of
  each project a unique prefix.
* ``--shortdescription``: shortens job descriptions and may dramatically
  improve configuration speed on complex recipes.
* ``-o artifacts.copy=archive``: requires that ``--upload`` and ``--download``
  are enabled. The Jenkins master will not be used to store the (intermediate)
  artifacts but everything is exchanged through the binary archive. Jenkins
  is not good at storing and serving big binary artifacts itself.
* ``-o jobs.update=lazy``: only update jobs if strictly necessary. This will
  improve the update time if only a subset of jobs is affected.

Make sure the seed job is always scheduled on the same server. Because the seed
job will only configure the Jenkins, it is possible to run it on the built-in
node.  If you do not want to use the build-in node you must make sure that only
a specific node is ever used. In any case, check "Restrict where this project
can be run" and enter an expression that matches only one node.

Reacting to changes in git/svn
==============================

Usually Jenkins is used to build a project continuously. If the recipes
reference branches instead of tags, it is desirable to trigger the build of
affected jobs on changes on these branches. There are two options to accomplish
this: either polling or by push notifications. Because Bob uses the git and
subversion plugins, there is no difference to other projects built on Jenkins
when building Bob projects.

The simplest solution is to instruct Jenkins to poll for SCM changes. You
can set the polling interval with the ``scm.poll`` extended option. The syntax
is the same as for ``cron`` with some minor extensions. See the
`Jenkins cron syntax <https://www.jenkins.io/doc/book/pipeline/syntax/#cron-syntax>`_
description in the Jenkins user handbook. The following will configure all
jobs in the project to poll the git/svn server every hour::

    $ bob jenkins set-options  local -o scm.poll="@hourly"
    $ bob jenkins push local

Even though polling is a simple solution, it has some important drawbacks. In
the worst case changes are detected only after a full polling interval has
elapsed. Additionally it can put significant load on the SCM server and might
even trigger rate limits. Thus the preferred solution is to forward triggers
from the SCM server to the Jenkins server. See the documentation of the `Git
Plugin Push Notifications
<https://plugins.jenkins.io/git/#plugin-content-push-notification-from-repository>`_
and the `Subversion Plugin Post-commit hooks
<https://plugins.jenkins.io/subversion/#plugin-content-post-commit-hook>`_ for
how to setup proper post-commit hooks. All jobs are always configured in a way
that they will react to matching commit hook messages. Except on the server
there is no additional configuration needed in Bob.

To test the automatic triggering, create yourself a small project based on the
following recipe that just clones a repository. Use a git repository where you
have write access and that can be used for testing purposes:

.. code-block:: yaml

   root: True
   checkoutSCM:
       scm: git
       url: ...
   buildScript: ""
   packageScript: ""

Build this recipe on a Jenkins server. It is assumed that you have followed the
git plugin documentation about commit hooks (see above) and that the Jenkins
server receives git notifications from the git server::

    $ bob jenkins add local http://... -r ...
    $ bob jenkins push local

After the job was built, change the git repositories master branch and push
your changes. Shortly after this, the Jenkins job will be rebuilt. The same
procedure will work for svn repositories too.

Note that it might still be a good idea to configure a very large polling
interval, e.g.  once per day. It will act as a safeguard if commit
notifications were lost, e.g.  due to short interruptions of network
connectivity, Jenkins updates or similar.

Collecting garbage
==================

Finally, as the project is built continuously, there is the need to limit
the amount of data that is created. In the most simple case, the project is
no longer needed and all jobs can be removed from the Jenkins server. This is
done by calling::

    bob jenkins prune local

which deletes all jobs tied to the ``local`` alias. If you want to keep the
end result(s) you may add ``--intermediate`` to keep at least the jobs building
root packages and delete everything else.

As a project is updated some recipes might gain or loose dependencies. This
will result in new jobs being created or obsolete jobs being deleted. If the
project used the ``--keep`` option it will accumulate obsolete jobs that are
left behind in a disabled state. You can remove those unneeded jobs by calling
``bob jenkins prune local --obsolete``.

To control the build logs and artifacts of the individual jobs, there are a
couple of extended options, all starting with the ``jobs.gc.`` prefix. By
default the build logs and artifacts of jobs building root packages are kept
forever.  It is therefore recommended to set the ``jobs.gc.root.artifacts`` and
``jobs.gc.root.builds`` options to some sensible values to limit the number of
builds being retained. Conversely, all non-root jobs only retain the last
successful build. If you want to retain more builds, just set
``jobs.gc.deps.artifacts`` and ``jobs.gc.deps.builds`` to some higher number.

Another source for disk usage are :ref:`shared <configuration-recipes-shared>`
packages. They are installed on first usage on each build node. Either
the shared location (defaults to ``${JENKINS_HOME}/bob``) must be deleted
manually from time to time, or a sensible limit is defined with the
``shared.quota`` option.

See also the :ref:`manpage-archive` command.
