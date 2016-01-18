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

Firing up a Jenkins
===================

You might let Bob configure a Jenkins server for you to build a project. Bob
requires that the following settings and plugins are available:

* Set "/bin/bash" as shell
* `Copy Artifact plugin`_
* `Subversion plugin`_
* `Git plugin`_
* `Multiple SCMs plugin`_

.. _Copy Artifact plugin: https://wiki.jenkins-ci.org/display/JENKINS/Copy+Artifact+Plugin
.. _Subversion plugin: https://wiki.jenkins-ci.org/display/JENKINS/Subversion+Plugin
.. _Git plugin: https://wiki.jenkins-ci.org/display/JENKINS/Git+Plugin
.. _Multiple SCMs plugin: https://wiki.jenkins-ci.org/display/JENKINS/Multiple+SCMs+Plugin

