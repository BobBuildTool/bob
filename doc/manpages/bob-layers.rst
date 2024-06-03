.. _manpage-layers:

bob-layers
==========

.. only:: not man

   Name
   ----

   bob-layers - Handle layers

Synopsis
--------

::

    bob show [-h] [-c CONFIGFILE] [-v] [-D DEFINES] {update,status}


Description
-----------

Update layers or show their scm-status. The following sub-commands are
available:

``update``

Updates the layers.

``status``

Show the scm-status of each layer and optionally list modifications. See
`man bob-status` for a description of the output fields.

Options
-------

``-c CONFIGFILE``
    Use additional configuration file.

    The ``.yaml`` suffix is appended automatically and the configuration file
    is searched relative to the project root directory unless an absolute path
    is given. Bob will parse these user configuration files after
    *default.yaml*. They are using the same schema.

    This option can be given multiple times. The files will be parsed in the
    order as they appeared on the command line.

``-D VAR=VALUE``
    Override default or set environment variable.

    Sets the variable ``VAR`` to ``VALUE``. This overrides the value possibly
    set by ``default.yaml``, config files passed by ``-c`` or any file that was
    included by either of these files.

``-v, --verbose``
    Increase verbosity (may be specified multiple times)
