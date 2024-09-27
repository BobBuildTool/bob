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

    bob layers [-h] [-c CONFIGFILE] [-lc LAYERCONFIG] [-v] [-D DEFINES]
               [--attic | --no-attic] 
               {update,status}

Description
-----------

Update layers or show their SCM-status. The following sub-commands are
available:

``update``
    Updates the layers.

``status``
    Show the SCM-status of each layer and optionally list modifications. See
    :ref:`bob status <manpage-bob-status>` for a description of the output
    fields.

Options
-------

``--attic``
    Move layer workspace to attic if inline SCM switching is not possible.
    (Default)

``--no-attic``
    Do not move layer workspace to attic if inline SCM switching is not possible.
    Instead a build error is issued.

``-c CONFIGFILE``
    Use additional configuration file.

    The ``.yaml`` suffix is appended automatically and the configuration file
    is searched relative to the project root directory unless an absolute path
    is given. Bob will parse these user configuration files after
    *default.yaml*. They are using the same schema.

    This option can be given multiple times. The files will be parsed in the
    order as they appeared on the command line.

``-lc LAYERCONFIG``
    Use additional layer configuration file.

    This is special kind of configuration file to control the layers checkout. Only
    ``layersWhitelist`` and ``layersScmOverrides`` are supported.

    The ``.yaml`` suffix is appended automatically and the configuration file
    is searched relative to the project root directory unless an absolute path
    is given.

``-D VAR=VALUE``
    Override default or set environment variable.

    Sets the variable ``VAR`` to ``VALUE``. This overrides the value possibly
    set by ``default.yaml``, config files passed by ``-c`` or any file that was
    included by either of these files.

``-v, --verbose``
    Increase verbosity (may be specified multiple times)
