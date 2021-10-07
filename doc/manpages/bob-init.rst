.. _manpage-bob-init:

bob-init
========

.. only:: not man

   Name
   ----

   bob-init - Initialize out-of-source build tree

Synopsis
--------

::

    bob init [-h] PROJECT [BUILD]


Description
-----------

Setup a build directory that builds the project located at ``PROJECT``. By
default the current directory is initialized. Optionally the build directory
can be specified as 2nd parameter ``BUILD``. The directories to the build
directory will be created if they do not exist yet.

See also
--------

:ref:`bob-build(1) <manpage-build>` :ref:`bob-dev(1) <manpage-dev>`
