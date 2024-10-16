.. _manpage-graph:

bob-graph
=========

.. only:: not man

   Name
   ----

   bob-graph - Generate dependency graphs

Synopsis
--------

::

    bob graph [-h] [-D DEFINES] [-c CONFIGFILE]
              [--sandbox | --slim-sandbox | --dev-sandbox | --strict-sandbox | --no-sandbox]
              [--destination DEST] [-e EXCLUDES] [-f FILENAME]
              [-H HIGHLIGHTS] [-n MAX_DEPTH] [-t {d3,dot}] [-o OPTIONS]
              PACKAGE [PACKAGE ...]

Description
-----------

Generate a dependency graph showing the dependencies of the given ``package``.
If no other options are given a interactive dependency graph is generated in
the ``graph`` subdirectory of the current working directory.

Two graph types are supported: ``d3`` and ``dot``.
The dot graph is helpfull for small projects or a very limited number of
dependencies, while the D3 graph is a html page using a javascript library
(www.d3js.org) to make a svg. This is interactive meaning it can be dragged
and zoomed. Nodes are clickable to highlight dependencies.

Options
-------

``-c CONFIGFILE``
    Use config File

``-D DEFINES``
    Override default environment variable

``--destination``
    Destination of graph output files.

``--dev-sandbox``
    Enable development sandboxing. Include sandbox dependencies in the graph.

``-e, --excludes``
    Do not show packages matching this regex. (And all it's
    dependencies)

``-f FILENAME, --filename FILENAME``
    Name of Outputfile.

``-H HIGHLIGHTS, --highlight HIGHLIGHTS``
    Highlight packages matching this regex.


``-n MAX_DEPTH, --max-depth MAX_DEPTH``
    Max depth. Show only the first ``n`` dependencies of ``package``.

``--no-sandbox``
    Disable sandboxing. The graph will not have sandbox dependencies. This is
    the default.

``--sandbox``
    Enable partial sandboxing. Include sandbox dependencies in the graph.

``--slim-sandbox``
    Enable slim sandboxing.

``--strict-sandbox``
    Enable strict sandboxing. Include sandbox dependencies in the graph.

``-t, --type``
    Set the graph type. ``d3`` (default) or ``dot``.

``-o OPTIONS``
    Set extended options. (See :ref:`bob-graph-extended-options` for the list of
    available options.

.. _bob-graph-extended-options:

Extended Options
----------------

The following options are available. Any unrecognized options are ignored.

D3-Graph
~~~~~~~~

d3.showScm
   Type: boolean
   Add the package scm's to the mouse-over box.

d3.localLib
   Use a local version of d3.v4.min.js. This is copied to the graph
   folder making it possible to use the graph offline.

d3.dragNodes
   Type: boolean
   Enable node drag functionality.
