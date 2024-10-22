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

    bob layers status [-h] [-lc LAYERCONFIG] [-D DEFINES] [--show-clean]
                      [--show-overrides] [-v]
    bob layers update [-h] [-lc LAYERCONFIG] [-D DEFINES]
                      [--attic | --no-attic] [-v]

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

``-lc LAYERCONFIG``
    Use additional layer configuration file.

    This is special kind of configuration file to control the layers checkout. Only
    ``layersWhitelist`` and ``layersScmOverrides`` are supported.

    The ``.yaml`` suffix is appended automatically and the configuration file
    is searched relative to the project root directory unless an absolute path
    is given. If multiple layer configuration files are passed, all files are
    parsed. Later files on the command line have higher precedence.

``-D VAR=VALUE``
    Override default or set environment variable.

    Sets the variable ``VAR`` to ``VALUE``. This overrides the value possibly
    set by ``default.yaml``, config files passed by ``-c`` or any file that was
    included by either of these files.

``--show-clean``
    Show the status of a layer even if unmodified. This includes
    ``--show-overrides``.

``--show-overrides``
    Show layers that have active :ref:`layersScmOverrides <configuration-config-layersScmOverrides>`
    (``O``) even if the layer is unchanged. Override information is always
    displayed if a layer is shown but a ``STATUS`` line is normally only
    emitted if the SCM was modified. Adding ``-v`` will additionally show the
    detailed override status.

``-v, --verbose``
    Increase verbosity (may be specified multiple times)
