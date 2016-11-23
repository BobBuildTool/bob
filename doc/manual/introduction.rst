Introduction
============

Bob is a build automation tool inspired by bitbake and portage. It's main
purpose is to build software packages, very much like packages in a Linux
distribution. It typically works on coarse entities i.e. not on individual
source files.

.. image:: /images/overview.png

In contrast to similar tools Bob tries to focus on the following requirements
that are special when building complex embedded systems:

* Holistic approach: Bob can be used to describe and build the whole software
  stack of a project. At the same time Bob can be used to build, change and
  test arbitrary parts of the project by involved developers.
* Cross compilation with multiple tool chains: Some of these tool chains may
  have to built during the build process. Bob can also be used to verify the
  build environment, override specific host tools or abort the process if some
  perquisites are not met.
* Reproducible builds: Bob aims to provide a framework which enables
  reproducible and even bit identical builds. To do so each package declares
  its required environment, tools and dependencies. With this information Bob
  executes the build steps in a controlled environment.
* Continuous integration: building from live branches and not just fixed
  tarballs is full supported. All packages are described in a declarative way.
  Using this information the packages can be built locally but also as separate
  jobs on a build server (e.g. Jenkins). Bob can track all dependencies between
  the packages and commits can trigger rebuilds of all affected packages.
* Variant management: because all packages declare their input environment
  explicitly Bob can compute if a package must be built differently or can be
  reused from another build.

All in all Bob is just a framework for the controlled execution of shell
scripts. To maximize reproducibility Bob tracks the environment and the input
of these scripts. If in doubt, Bob will rebuild the (supposedly) changed
package.

What sets Bob apart from other systems is the functional approach. Bob takes
the input for each package and processes the instructions to build the result,
very much like a (imperfect) mathematical function. Every package is kept
separately and only declared dependencies are available to the package build
scripts.

In contrast to that typical other package build systems describe dependencies
that must be satisfied in a shared root file system. This ensures that required
files are present at the known locations but it is perfectly ok that more is
there. Bob on the other hand has no concept of "installation". Packages are
computed with their scripts and from the declared input.

