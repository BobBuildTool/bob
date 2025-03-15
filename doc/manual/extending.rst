.. highlight:: python

Extending Bob
=============

Bob may be extended through plugins. Right now the functionality that can be
tweaked through plugins is intentionally limited. If you can make a case what
should be added to the plugin interface, please open an issue at GitHub or
write to the mailing list.

See ``contrib/plugins`` in the Bob repository for some examples.

.. _extending-plugins:

Plugins
-------

Plugins can be put into a ``plugins`` directory as .py files. A plugin is only
loaded when it is listed in ``config.yaml`` in the
:ref:`configuration-config-plugins` section.  Each plugin must provide a
'manifest' dict that must have at least an 'apiVersion' entry. The apiVersion
is compared to the Bob version and must not be greater for the plugin to load.
At minimum this looks like this::

    manifest = {
        'apiVersion' : "0.2"
    }

Class documentation
-------------------

Plugins might only access the following classes with the members documented in
this manual. All other parts of the bob Python package namespace are considered
internal and might change without notice.

.. autoclass:: bob.input.PluginProperty
   :members:

.. autoclass:: bob.input.PluginSetting
   :members:

.. autoclass:: bob.input.PluginState()
   :members:

.. autoclass:: bob.input.RecipeSet()
   :members:

.. autoclass:: bob.input.Recipe()
   :members:

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

Path formatters are responsible for calculating the workspace path of a
package. There are three plugin hooks that override the path name calculation:

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

Additionally there is a special hook (``developNamePersister``) that is
responsible to create a surjective mapping between steps and workspace paths
with the restriction that different variant ids must not be mapped to the same
directory. The hook function is taking the configured develop name formatter
(see above) and is expected to return a callable name formatter too. The
``developNamePersister`` must handle two cases in the following way:

* The passed name formatter returns different paths for steps that have the
  same variant id. In this case the ``developNamePersister`` should only return
  one such path for the same variant id.
* The name formatter returns the same path for different variant ids. In this
  case the ``developNamePersister`` must disambiguate the path (e.g. by adding
  a unique suffix) to return different paths for the different variants of the
  step(s).

Even though it is not strictly required by Bob it is highly recommended to map
all steps with the same variant id to a single directory. The hook is currently
only available for the develop mode. The default implementation in Bob is to
append an incrementing number starting by one for each variant to the path
returned by the configured name formatter::

    def developNamePersister(nameFormatter):
        dirs = {}

        def fmt(step, props):
            baseDir = nameFormatter(step, props)
            digest = step.getVariantId()
            if digest in dirs:
                res = dirs[digest]
            else:
                num = dirs.setdefault(baseDir, 0) + 1
                res = os.path.join(baseDir, str(num))
                dirs[baseDir] = num
                dirs[digest] = res
            return res

        return fmt

    manifest = {
        'apiVersion' : "0.1",
        'hooks' : {
            'developNamePersister' : developNamePersister
        }
    }

If your name formatter generates unique names for each variant of the steps you
may want to override the persister to change this behavior, e.g. to not add a
number for the first variant.

.. _extending-hooks-string:

String functions
~~~~~~~~~~~~~~~~

String functions can be invoked from any place where string substitution as
described in :ref:`configuration-principle-subst` is allowed. These functions
are called with at least one positional parameter for the arguments that were
specified when invoking the string function. They are expected to return a
string and shall have no side effects. The function has to accept any number of
additional keyword arguments. Currently the following additional kwargs are
passed:

* ``env``: dict of all available environment variables at the current context
* ``recipe``: the current :class:`bob.input.Recipe`
* ``sandbox``: ``True`` if a sandbox *image* is used. ``False`` if no sandbox image was
  configured or if it is disabled (e.g. ``--no-sandbox`` option was specified).

In the future additional keyword args may be added without notice. Such string
functions should therefore have a catch-all ``**kwargs`` parameter. A sample implementation
could look like this::

    def echo(args, **kwargs):
        return " ".join(args)

    manifest = {
        'apiVersion' : "0.2",
        'stringFunctions' : {
            "echo" : echo
        }
    }

.. _extending-plugins-jenkins:

Jenkins job mangling
~~~~~~~~~~~~~~~~~~~~

Jenkins jobs that are created by Bob are very simple and contain only
information that was taken from the recipes. It might be necessary to enable
additional plugins, add build steps or alter the job configuration in special
ways. For such use cases the following hooks are available:

 * ``jenkinsJobCreate``: initial creation of a job
 * ``jenkinsJobPreUpdate``: called before updating a job config
 * ``jenkinsJobPostUpdate``: called after updating a job config

All hooks take a single mandatory positional parameter: the job config XML as
string. The hook is expected to return the altered config XML as string too.
The function has to accept any number of additional keyword arguments.
Currently the following additional kwargs are passed:

 * ``alias``: alias name used for jenkins
 * ``buildSteps``: list of all build steps (:class:`bob.input.Step`) used in
   the job
 * ``checkoutSteps``: list of all checkout steps (:class:`bob.input.Step`) used
   in the job
 * ``hostPlatform``: Jenkins host platform type (``linux``, ``msys`` or ``win32``)
 * ``name``: name of Jenkins job
 * ``nodes``: The nodes where the job should run
 * ``packageSteps``: list of all package steps (:class:`bob.input.Step`) used
   in the job
 * ``prefix``: Prefix of all job names
 * ``sandbox``: Boolean whether sandbox should be used. Plugins starting with
   API version ``0.25`` are passed the sandbox mode as string (``no``, ``yes``,
   ``slim``, ``dev`` or ``strict``).
 * ``url``: URL of Jenkins instance
 * ``windows``: True if Jenkins runs on Windows

See the jenkins-cobertura plugin in the `contrib <https://github.com/BobBuildTool/bob/tree/master/contrib>`_ directory for an example. The
default implementation in Bob looks like this::

    def jenkinsJobCreate(config, **info):
        return config

    def jenkinsJobPreUpdate(config, **info):
        return config

    def jenkinsJobPostUpdate(config, **info):
        return config

    manifest = {
        'apiVersion' : "0.4",
        'hooks' : {
            'jenkinsJobCreate' : jenkinsJobCreate,
            'jenkinsJobPreUpdate' : jenkinsJobPreUpdate,
            'jenkinsJobPostUpdate' : jenkinsJobPostUpdate
        }
    }

.. _extending-generators:

Generators
----------

The main purpose of a generator is to generate project files for one or more IDEs.
There are several built-in generators, e.g. for QtCreator project files.

A generator is called with at least 3 arguments:

* ``package``: the :class:`bob.input.Package` to build the project for.
* ``argv``: Arguments not consumed by ``bob project``.
* ``extra``: Extra arguments to be passed back to ``bob dev`` when called from
  the IDE. These are the generic arguments that ``bob project`` parses for all
  generators.

Starting with Bob 0.17 an additional 4th argument is passed to the generator
function:

* ``bob``: The fully qualified path name to the Bob executable that runs the
  generator. This may be used to generate project files that work even if Bob
  is not in $PATH.

The presence of the 4th parameter is determined by the ``apiVersion`` of the
manifest.

A simple generator may look like::

    def nullGenerator(package, argv, extra, bob):
        return 0

    manifest = {
        'apiVersion' : "0.17",
        'projectGenerators' : {
            'nullGenerator' : nullGenerator,
        }
    }

Traditionally a generator handles only one package. When running the generator
this package needs to be provided using the complete path. Starting with Bob
0.23 a generator can specify that he can handle multiple packages as a result
of a :ref:`package query <manpage-bobpaths>`. This is done by setting the
optional `query` property to `True`. In this case the first argument of the
generator are the package objects returned by
:func:`bob.pathspec.PackageSet.queryPackagePath`.::

   def nullQueryGenerator(packages, argv, extra, bob):
       for p in packages:
           print(p.getName())
       return 0

   manifest = {
        'apiVersion' : "0.23",
        'projectGenerators' : {
            'nullQueryGenerator' : {
               'func' : nullQueryGenerator,
               'query' : True,
        }
    }


.. _extending-settings:

Plugin settings
---------------

Sometimes plugin behaviour needs to be configurable by the user. On the other
hand Bob expects plugins to be deterministic. To have a common interface for
such settings it is possible for a plugin to define additional keywords in the
:ref:`configuration-config-usr`. This provides Bob with the information to
validate the settings and detect changes in a reliable manner.

To define such settings the plugin must derive from
:class:`bob.input.PluginSetting`, create an instance of that class and store it
in the manifest under ``settings``. A minimal example looks like the
following::

    from bob.input import PluginSetting

    class MySettings(PluginSetting):
        @staticmethod
        def validate(data):
            return isinstance(data, str)

    mySettings = MySettings("")

    manifest = {
        'apiVersion' : "0.14",
        'settings' : {
            'MySettings' : mySettings
        }
    }

This will define a new, optional "MySettings" keyword for the user
configuration that will accept any string. The default value, if nothing is
configured in ``default.yaml``, is specified when constructing ``MySettings``.
In the above example it is an empty string.

.. attention::
    Do not configure your plugins by any other means. Bob will not detect
    changes and, due to aggressive caching, might not call the plugin again to
    process the new settings. So reading external files or using environment
    variables results in undefined behavior.

It is not possible to re-define already existing setting keywords. This applies
both to Bob built-in settings as well as settings defined by other plugins.
Because Bob is expected to define new settings in the future a plugin defined
setting must not start with a lower case letter. These names are reserved for
Bob.

Custom recipe properties
------------------------

A plugin may define any number of additional recipe properties. A property
describes a key in a recipe that is parsed. The class handling the property is
responsible to validate the data in the recipe and store the value. It must be
derived from :class:`bob.input.PluginProperty`. The property class handles the
inheritance between recipes and classes too.

The following example shows two trivial properties::

    class StringProperty(PluginProperty):
        @staticmethod
        def validate(data):
            return isinstance(data, str)

    manifest = {
        'apiVersion' : "0.21",
        'properties' : {
            "CheckoutDir" : StringProperty,
            "Platform" : StringProperty
        },
    }

The above example defines two new keywords in recipes: ``CheckoutDir`` and
``Platform``. As verified by the ``validate`` method, they need to be strings.
Because :func:`bob.input.PluginProperty.inherit` was not overridden, the recipe
and higher priority classes will simply replace the value of lower priority
classes. Other plugin extensions can query the value of a property by calling
:func:`bob.input.Recipe.getPluginProperties` to fetch the instances of a
particular recipe. For example this might be used by project generators to
supply recipe specific data to the generator.

If custom properties need to be propagated in the recipe dependency hierarchy,
a *property state tracker* is required. A state tracker is a class that is
invoked on every step when walking the dependency tree to instantiate the
packages. The state tracker thus has the responsibility to calculate the final
values associated with the properties for every package. Like properties, there
can be more than one state tracker. Any state tracker provided by a plugin must
be derived from :class:`bob.input.PluginState`.

It is not possible to re-define already existing recipe properties. This
applies both to Bob built-in properties as well as properties defined by other
plugins.  Because Bob is expected to define new settings in the future, a
plugin defined properties must not start with a lower case letter. These names
are reserved for Bob.
