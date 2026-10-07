.. _manpage-bob-ls:

bob-ls
======

.. only:: not man

   Name
   ----

   bob-ls - List package hierarchy

Synopsis
--------

::

    bob ls [-h] [-a] [-A] [-o] [-r] [-u] [-p | -d] [-D DEFINES]
           [-c CONFIGFILE]
           [--sandbox | --slim-sandbox | --dev-sandbox | --strict-sandbox | --no-sandbox]
           [package]

Description
-----------

List package dependencies. The optional ``package`` argument specifies what
package(s) should be listed. If no package is specified the virtual root
package is used, thus printing all top level packages and aliases. The ``/``
package path selects the virtual root package too but does not list aliases as
it is an absolute location path. See :ref:`bobpaths(7) <manpage-bobpaths>` for
how to specify packages and how aliases are handled.

By default only the declared dependencies of the package are displayed. By
adding ``-a`` the injected dependencies (i.e. dependencies provided by its
declared dependencies through :ref:`provideDeps
<configuration-recipes-providedeps>`) are displayed too. To see the relative
path from where the injected dependencies originate add ``-o``. See
:ref:`configuration-principle-dependencies` for the definition of these terms.

Without any further options only the first level of dependencies is listed.
Adding ``-r`` shows a graphical tree of all transitive dependencies too.
Recursion follows the same dependencies that are displayed, i.e. only declared
dependencies unless ``-a`` is given. To get
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

To see *every* package selected by the query, add ``-A``. This will print all
alternate paths to identical packages. This affects only the ``-d`` and ``-p``
options, because the path leading to the selected packages is significant.

Options
-------

``-a, --all``
    Show injected dependencies too. By default only declared dependencies (i.e.
    dependencies explicitly specified in the recipe) are displayed.

``-A, --alternates``
    For listings that print the full path of packages (``-d``, ``-p``), display
    all packages, including identical ones. By default only unique packages,
    that were selected by the query, are displayed.

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

``--dev-sandbox``
    Enable development sandboxing.

``--no-sandbox``
    Disable sandboxing

``-o, --origin``
    Show origin of injected dependencies. This is printed as relative path to
    the current package.

``-p, --prefixed``
    Prints the full path prefix for each package. Without this option a
    graphical tree of the dependencies is displayed.

``-r, --recursive``
    Recursively display dependencies

``--sandbox``
    Enable partial sandboxing.

``--slim-sandbox``
    Enable slim sandboxing.

``--strict-sandbox``
    Enable strict sandboxing.

``-u, --unsorted``
    Show the packages in the order they were named in the recipe. By default
    they are sorted by name for better readability.

See also
--------

:ref:`bobpaths(7) <manpage-bobpaths>`
