bob-query-scm
=============

.. only:: not man

   Name
   ----

   bob-query-scm - Query SCM information

Synopsis
--------

::

    bob query-scm [-h] [-f FORMATS] [--default DEFAULT] [-r] package

Description
-----------

Query SCM configuration of packages.

By default this command will print one line for each SCM in the given package.
The output format may be overridded by '-f'. By default the following formats
are used:

 * git="git {package} {dir} {url} {branch}"
 * svn="svn {package} {dir} {url} {revision}"
 * cvs="cvs {package} {dir} {cvsroot} {module}"
 * url="url {package} {dir}/{fileName} {url}"

Options
-------

``--default DEFAULT``
    Default for missing attributes (default: "")

``-f FORMATS``
    Output format for scm (syntax: scm=format). Can be specified multiple times.

``-r, --recursive``
    Recursively display dependencies

