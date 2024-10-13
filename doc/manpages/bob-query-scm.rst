.. _manpage-bob-query-scm:

bob-query-scm
=============

.. only:: not man

   Name
   ----

   bob-query-scm - Query SCM information

Synopsis
--------

::

    bob query-scm [-h] [-D DEFINES] [-c CONFIGFILE] [-f FORMATS]
                  [--default DEFAULT] [-r]
                  [--sandbox | --slim-sandbox | --dev-sandbox | --strict-sandbox | --no-sandbox]
                  packages [packages ...]

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

``-c CONFIGFILE``
    Use config File

``-D DEFINES``
    Override default environment variable

``--default DEFAULT``
    Default for missing attributes (default: "")

``--dev-sandbox``
    Enable development sandboxing.

``-f FORMATS``
    Output format for scm (syntax: scm=format). Can be specified multiple times.

``--no-sandbox``
    Disable sandboxing

``-r, --recursive``
    Recursively display dependencies

``--sandbox``
    Enable partial sandboxing.

``--slim-sandbox``
    Enable slim sandboxing.

``--strict-sandbox``
    Enable strict sandboxing.
