Build a demo project
********************

Prequisites
===========

You should have Bob somewhere in your ``$PATH``. As the first step clone the
tutorial projects::

    $ git clone https://github.com/BobBuildTool/bob-tutorials.git
    $ cd bob-tutorials/sandbox

This tutorial will use the sandbox example that should work basically
everywhere. The next steps are all executed in the same directory.

Build release
=============

To see the packages that you can build type::

    $ bob ls -r
    vexpress
        sandbox::debian-8.2-x86
        toolchain::x86
        toolchain::arm-linux-gnueabihf
        initramfs
            busybox
                toolchain::make
        linux-image
            toolchain::make

As you can see there is a single top-level package called ``vexpress``. This
demo project builds a QEMU image for the ARM Versatile Express machine. To
build the project simply do the following::

    $ bob build vexpress

This will fetch the toolchains and sources and will build the image locally in
the ``work`` directory. Grab a coffee and after some time the build should
finish::

    ...
    >> vexpress
       BUILD     work/vexpress/build/1/workspace
       PACKAGE   work/vexpress/dist/1/workspace
    Build result is in work/vexpress/dist/1/workspace

Now you can run the example if you have QEMU installed::

    $ ./work/vexpress/dist/1/workspace/run.sh

Some words about the directory layout. The general layout is
``work/<package>/{src,build,dist}/#/`` where:

* ``<package>`` is the name of the package. In case of namespaces that might
  be several subdirectories, e.g. toolchain::arm-linux-gnueabihf will be built
  in ``work/toolchain/arm-linux-gnueabihf/...``.
* ``{src,build,dist}`` corresponds to the result of the step, e.g. ``src`` is
  where the checkout step is run.
* ``#`` is a sequential number starting from 1 that is increased for every
  variant of the package. New variants can emerge as recipes are updated.

Under the directory of a package you can find the following files and
directories:

* ``workspace/``: This is the directory where the step is executed and that
  holds the result.
* ``{checkout,build,package}.sh``: A wrapper script that executes this specific
  step. Running the script will execute this particular step again. If you call
  the script with ``shell`` as first argument a new shell is spawned with
  exactly the same environment as the step script would find.
* ``script``: The actual script that was computed from the recipe and the
  inherited classes. This is not directly executable because it expects the
  right environment and arguments.
* ``log.txt``: Logs of all runs.

Development build
=================

When executing ``bob build`` you build the requested packages in *release
mode*. This mode is intended for reproducible builds. The example already
employs a sandbox so that no host tools or paths are used. Though this is great
for binary reproducible builds it is inconvenient to debug and incrementally
change parts of the project.

For this purpose you can build the project in *development mode*. This mode
basically does the same things but with some important differences:

* Sandboxes are not used by default. The host should have the required
  development tools installed, for example make, gcc, perl and so on. You may
  still build inside a sandbox by adding the ``--sandbox`` option.
* Different directory layout to group sources, build and results of all
  packages: ``dev/{src,build,dist}/<package>/#/``.
* Incremental builds.
* Stable directory tree with incremental updates to the workspace.

In this mode it is possible to build packages, make some changes and rebuild.
If your environment has the right tools installed you should get the same
result as the release mode. But because sandboxes are not used it is still
possible to debug the created binaries. So let's build the kernel in
development mode::

    $ bob dev vexpress/linux-image
    >> vexpress/linux-image
    >> vexpress/toolchain::arm-linux-gnueabihf
       CHECKOUT  dev/src/toolchain/arm-linux-gnueabihf/1/workspace
    ...
    >> vexpress/linux-image
       CHECKOUT  dev/src/linux-image/1/workspace
       BUILD     dev/build/linux-image/1/workspace
       PACKAGE   dev/dist/linux-image/1/workspace
    Build result is in dev/dist/linux-image/1/workspace

Notice that the development mode builds in a separate directory: ``dev``. The
numbering beneath the package name directory is kept stable. The numbers
represent only the currently possible variants of the package from the recipes.
If the ``checkoutSCM`` in the recipe is changed the old checkout will be moved
aside instead of using a new directory like in the release mode.

Suppose we want to make a patch to the kernel. This is as simple as to go to
``dev/src/linux-image/1/workspace``, edit some files and call Bob again to
rebuild::

    $ vi dev/src/linux-image/1/workspace/linux-4.3.3/...
    $ bob dev vexpress/linux-image

Bob will detect that there are changes in the sources of the kernel and make an
incremental build. For the sake of simplicity we might rebuild the top-level package
to test the full build::

    $ bob dev vexpress
    $ ./dev/dist/vexpress/1/workspace/run.sh

.. note::
   Touching (``touch ...``) source files will not have any effect. Bob detects
   changes purely by its content and not by looking on the file meta data.

Now that we have a kernel we might want to change the kernel configuration and
rebuild the kernel with the new one. From the output you can see that the
kernel was built in ``dev/build/linux-image/1/workspace``. We might edit the
``.config`` there directly but using ``make menuconfig`` is much more
convenient::

    $ ./dev/build/linux-image/1/build.sh shell -E
    $ make menuconfig

Now make and save your changes. Then rebuild the kernel::

    ...
      HOSTLD  scripts/kconfig/mconf
    scripts/kconfig/mconf  Kconfig
    configuration written to .config

    *** End of the configuration.
    *** Execute 'make' to start the build or try 'make help'.

    $ make -j $(nproc) bzImage

If you know how grab the kernel image directly out of the build tree and test
it. Alternatively you can rebuild the top-level package ::

    $ bob dev vexpress

and test the whole QEMU image. The choice is yours.

.. warning::
   Making changes to the build step tree is only detected by Bob in development
   mode. These changes should be properly saved in the sources or the recipe
   before moving on. Otherwise you risk that your changes are wiped out if Bob
   determines that a clean build is needed (e.g. due to recipe changes).

Query SCM status
================

After you have developed a great new feature you may want to know which sources you
have touched to commit them to a SCM. Bob offers ``bob status <options> <package>`` 
to show a list of SCM which are unclean. SCMs are unclean in case they have modified files,
unpushed commits, switched URLs or non matching tags or commit ids.

The output looks like the following line::

    STATUS <status code> <scm path> 

Status codes:

* ``U`` : Unpushed commits (Git only)
* ``u`` : unpushed commits on local branch (Git only)
* ``M`` : Modified sources.
* ``S`` : Switched. Could be different tag, commitId, branch or URL.


Firing up a Jenkins
===================

You might let Bob configure a Jenkins server for you to build a project. Bob
requires that the following plugins are available:

* `Conditional BuildStep Plugin`_: used to efficiently support shared packages
* `Copy Artifact plugin`_: used to carry results between the different jobs
* `Git plugin`_: to clone git repositores
* `Multiple SCMs plugin`_: used to support recipes that have multiple checkouts
* `Subversion plugin`_: to checkout SVN modules
* `Workspace Cleanup Plugin`_: to make clean builds if requested

.. _Copy Artifact plugin: https://wiki.jenkins-ci.org/display/JENKINS/Copy+Artifact+Plugin
.. _Subversion plugin: https://wiki.jenkins-ci.org/display/JENKINS/Subversion+Plugin
.. _Git plugin: https://wiki.jenkins-ci.org/display/JENKINS/Git+Plugin
.. _Multiple SCMs plugin: https://wiki.jenkins-ci.org/display/JENKINS/Multiple+SCMs+Plugin
.. _Conditional BuildStep Plugin: https://wiki.jenkins-ci.org/display/JENKINS/Conditional+BuildStep+Plugin
.. _Workspace Cleanup Plugin: https://wiki.jenkins-ci.org/display/JENKINS/Workspace+Cleanup+Plugin

Additionally some of the Bob helper tools must be installed on the Jenkins
server and be available in the PATH. The ``bob-hash-engine`` script is always
needed.  If you're using the sandbox feature ``bob-namespace-sandbox`` must be
available too. To keep the setup simple it is recommended to install Bob
entirely on the server.

Suppose you have a suitable Jenkins server located at
http://jenkins.intranet.local:8080. Go to the recipes directory and tell Bob
about your server and what you want to build there (substitute ``<user>`` and
``<pass>`` with your actual credentials)::

    $ bob jenkins add intranet http://<user>:<pass>@jenkins.intranet.local:8080 -p sandbox- -r vexpress

This adds a synonym ("intranet") for your Jenkins server. The ``-p`` adds the
``sandbox-`` prefix to every job. At least one ``-r`` option must be given to
specify what should be built. To view the settings type::

    $ bob jenkins ls -vv
    intranet
     URL: http://<user>:<pass>@jenkins.intranet.local:8080/
     Prefix: sandbox-
     Upload: disabled
     Sandbox: disabled
     Roots: vexpress
     Jobs: 

As you can see there is no job configured yet on the server. This is done by ::

    $ bob jenkins push intranet

which pushes the local state of the recipes as Jenkins jobs to the server. Note
that Bob does not need to be available on the server. The content of the
recipes is inserted as shell steps into the jobs with special prologues to
accommodate for the special environment.

If all required tools and plugins have been installed on Jenkins the build
should succeed. Go into the "sandbox-vexpress" job, download the archived
artifacts and run them locally.

Using IDEs with Bob
===================

You may want to use a IDE with Bob. At the moment QTCreator and Eclipse are
supported. You can add more IDE's using :ref:`extending-generators` extension.
To generate project files the basic call is::

    $ bob project <genericArgs> <generator> <package> <specificArgs>

with ``genericArgs``:

* ``-n``: Do not build. Usually bob project builds the given package first to
  be able to collect binaries and add them to the IDEs run/debug targets.
* ``-D -c -e -E``: These arguments will be passed to bob dev and will also be
  used when compiling from IDE.

with ``generator``:

* ``eclipseCdt``: Generate project files for eclipse. Tested with eclipse MARS.
* ``qt-creator``: Generate project files for QtCreator. Tested with 4.0 and 4.1.

and ``package`` which is the name of a package to generate the project for.
Usually all dependencies for this package will be visible in the IDE. The
``specificArgs`` arguments are used by the generator itself. They differ from
generator to generator (see below).

QTCreator
---------

QtCreator specific Arguments:

* ``--destination``: destination directory for the project files. Default is
  <workingDir>/projects/package_stack.
* ``--name``: name of the project. Default is packageName.
* ``-I``: additional include directories. They will only be added for indexer
  and will not change the buildresult.
* ``-f``: additional files. Normally only c[pp] and h[pp] files will be added.
  You can add more files using a regex.
* ``--kit``: kit to use for this project. You may want to use a different
  sysroot for includes and buildin preprocessor settings from your compiler. To
  tell QtCreator which toolchain to use you need to specify a kit. There are at
  least two options to create a kit: using the GUI or the sdkTools.

The following example shows how to create a cross compiling project for the
sandbox-tutorial and the included arm-toolchain: ::

        $ sdktool addTC \
            --id "ProjectExplorer.ToolChain.Gcc:arm" \
            --name "ARM-Linux-Gnueabihf" \
            --path "<toolchain-dist>/gcc-linaro-arm-linux-gnueabihf-4.9-2014.09_linux/bin/arm-linux-gnueabihf-g++" \
            --abi arm-linux-generic-elf-32bit
        $ sdktool addDebugger \
            --id "gdb:ARM32" \
            --name "ARM-gdb" \
            --binary <toolchain-dist>/gcc-linaro-arm-linux-gnueabihf-4.9-2014.09_linux/bin/arm-linux-gnueabihf-gdb
        $ sdktool addKit \
            --id "ARM_Linux" \
            --name "ARM Linux Gnueabi" \
            --devicetype Desktop \
            --toolchain "ProjectExplorer.ToolChain.Gcc:arm" \
            --sysroot <toolchain-dist>/gcc-linaro-arm-linux-gnueabihf-4.9-2014.09_linux/arm-linux-gnueabihf/libc/ \
            --debuggerid "gdb:ARM32"
        $ bob project qtcreator vexpress --kit ARM_LINUX

EclipseCdt
----------

Eclipse specificArgs:

* ``--destination``: destination directory for the project files. Default is
  <workingDir>/projects/package_stack.
* ``--exclude``: eclipse indexer sometimes runs OutOfMemory on large
  sourcetrees.  You can specify package names (or use a regular expression) to
  define packages excluded from build. This will stop indexer from indexing
  these packages.
* ``--name``: name of the project. Default is packageName.
* ``-I``: additional include directories. They will only be added for indexer
  and will not change the buildresult.

