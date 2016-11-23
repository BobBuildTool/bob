bob-clean
=========

.. only:: not man

   Name
   ----

   bob-clean - Delete unused src/build/dist paths of release build

Synopsis
--------

::

    bob clean [-h] [--dry-run] [-s] [-v]


Description
-----------

The *bob clean* command removes currently unused directories from previous
:doc:`bob-build` invocations.  By default only 'build' and 'package' steps are
evicted. Adding ``-s`` will clean 'checkout' steps too. Make sure that you have
checked in (and pushed) all your changes, tough. When in doubt add
``--dry-run`` to see what would get removed without actually deleting that
already.


Options
-------

``--dry-run``
    Don't delete, just print what would be deleted

``-s, --src``
    Clean source steps too

``-v, --verbose``
    Print what is done

