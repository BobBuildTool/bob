bob-ls
======

.. only:: not man

   Name
   ----

   bob-ls - List package hierarchy

Synopsis
--------

::

    bob ls [-h] [-a] [-o] [-r] [-u] [-p | -d] [-D DEFINES] [-c CONFIGFILE]
           [--sandbox | --no-sandbox]
           [package]


Description
-----------

List package dependencies. The optional ``package`` argument specifies what
package(s) should be listed. If no package is specified the virtual root
package is used, thus printing all top level packages and aliases. The ``/``
package path selects the virtual root package too but does not list aliases as
it is a absolute location path. See :ref:`bobpaths(7) <manpage-bobpaths>` for
how to specify packages and how aliases are handled.

By default only the direct dependencies of the package are displayed. By adding
``-a`` the indirect dependencies (i.e. dependencies collected from
:ref:`provideDeps <configuration-recipes-providedeps>`) are displayed too. To
see the relative path from where the indirect dependencies were inherited add
``-o``.

Without any further options only the first level of dependencies is listed.
Adding ``-r`` shows a graphical tree of all transitive dependencies too. To get
a list of all transitive dependencies instead, specify ``-p``. This will print
each package on a separate line with the full package path. The aliases listed
below the virtual root package are not recursively traversed as they can
involve arbitrarily complex queries. If you want to recursively list the
dependencies of an alias you have to specify it explicitly as ``package``
argument.

Listing the dependencies of the selected package(s) is not always desired. To
see the selected packages of a complex query directly add ``-d``. This will
print the path of all *unique* packages that were selected by the query. This
cannot be used in conjunction with the ``-p`` option and ignores further ``-a``,
``-o`` and ``-r`` options.

Options
-------

``-a, --all``
    Show indirect dependencies too. By default only direct dependencies (i.e.
    dependencies explicitly specified in the recipe) are displayed.

``-c CONFIGFILE``
    Use config File

``-d, --direct``
    List packages themselves, not their contents. This comes in handy if the
    actual result of a query shall be displayed instead of the dependencies of
    the selected package(s). Cannot be used at the same time as ``-p``. The
    ``-a``, ``-o`` and ``-r`` options will have no effect if ``-d`` is
    specified.

``-D DEFINES``
    Override default environment variable

``--no-sandbox``
    Disable sandboxing

``-o, --origin``
    Show origin of indirect dependencies. This is printed as relative path to
    the current package.

``-p, --prefixed``
    Prints the full path prefix for each package. Without this option a
    graphical tree of the dependencies is displayed.

``-r, --recursive``
    Recursively display dependencies

``--sandbox``
    Enable sandboxing

``-u, --unsorted``
    Show the packages in the order they were named in the recipe. By default
    they are sorted by name for better readability.

See also
--------

:ref:`bobpaths(7) <manpage-bobpaths>`
