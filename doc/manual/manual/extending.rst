Extending Bob
=============

Bob may be extended through plugins. Right now the functionaly that can be
tweaked through plugins is quite limited. If you can make a case what should be
added to the plugin interface open an issue at GitHub or write to the mailing
list.

See ``contrib/plugins`` in the Bob repository for an example.

.. _extending-plugins:

Plugins
-------

Plugins can be put into a ``plugins`` directory as .py files. A plugin is only
loaded when it is listed in ``config.yaml`` in the
:ref:`configuration-config-plugins` section.  Each plugin must provide a
'manifest' dict that must have at least an 'apiVersion' entry. The current
version is "0.1". At minimum this looks like this::

    manifest = {
        'apiVersion' : "0.1"
    }

A plugin may define any number of properties and state trackers.

A property describes a key in a recipe that is parsed. The class handling the
property is responsible to validate the data in the recipe and store the value.
It must be derived from :class:`bob.input.PluginProperty`
The property class handles to inheritance between recipes and classes too.

A state tracker is a class that is invoked on every step when walking the
dependency tree to instantiate the packages. The state tracker thus has the
responsibility to calculate the final values associated with the properties for
every package. Like properties there can be more than one state tracker. Any
state tracker provided by a plugin must be derived from
:class:`bob.input.PluginState`-

Class documentation
-------------------

Plugins might only access the following classes with the members documented in
this manual. All other parts of the bob Python package namespace are considered
internal and might change without notice.

.. autoclass:: bob.input.PluginProperty
   :members:

.. autoclass:: bob.input.PluginState()
   :members:

.. autoclass:: bob.input.Recipe()
   :members: getName, getPackageName, isRoot

.. autoclass:: bob.input.Package()
   :members:

.. autoclass:: bob.input.Step()
   :members:

.. autoclass:: bob.input.Sandbox()
   :members:

.. autoclass:: bob.input.Tool()
   :members:

Hooks
-----

Path formatters
~~~~~~~~~~~~~~~

There are three plugin hooks that override the path name calculation:

 * releaseNameFormatter: local build in release mode
 * developNameFormatter: local build in development mode
 * jenkinsNameFormatter: Jenkins builds

All hooks must be a function that return the relative path for the workspace.
The function gets two parameters: the step for which the path should be
returned and a dictionary to the state trackers of the package that is
processed. The default implementation in Bob looks like this::

    def releaseNameFormatter(step, properties):
        return os.path.join("work", step.getPackage().getName().replace('::', os.sep),
                            step.getLabel())

    def developNameFormatter(step, properties):
        return os.path.join("dev", step.getLabel(),
                            step.getPackage().getName().replace('::', os.sep))

    def jenkinsNameFormatter(step, props):
        return step.getPackage().getName().replace('::', "/") + "/" + step.getLabel()

    manifest = {
        'apiVersion' : "0.1",
        'hooks' : {
            'releaseNameFormatter' : releaseFormatter,
            'developNameFormatter' : developFormatter,
            'jenkinsNameFormatter' : jenkinsNameFormatter
        }
    }

