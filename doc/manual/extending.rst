Extending Bob
=============

Bob may be extended through plugins. Right now the functionaly that can be
tweaked through plugins is quite limited. If you can make a case what should be
added to the plugin interface open an issue at GitHub or write to the mailing
list.

See ``contrib/plugins`` in the Bob repository for an example.

Another extension are generators. See :ref:`extending-generators`

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
additionaly keyword arguments.  Currently the following additionaly kwargs are
passed:

* ``env``: dict of all available environment variables at the current context
* ``recipe``: the current :class:`bob.input.Recipe`
* ``sandbox``: an instance of :class:`bob.input.Sandbox` of the current context
  or ``None`` if no sandbox was configured
* ``tools``: dict of all available tools (see :class:`bob.input.Tool`)
* ``stack``: list of package names that lead to the currently processed package

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
The function has to accept any number of additionaly keyword arguments.
Currently the following additionaly kwargs are passed:

 * ``alias``: alias name used for jenkins
 * ``buildSteps``: list of all build steps (:class:`bob.input.Step`) used in
   the job
 * ``checkoutSteps``: list of all checkout steps (:class:`bob.input.Step`) used
   in the job
 * ``name``: name of Jenkins job
 * ``nodes``: The nodes where the job should run
 * ``packageSteps``: list of all package steps (:class:`bob.input.Step`) used
   in the job
 * ``prefix``: Prefix of all job names
 * ``sandbox``: Boolean wether sandbox should be used
 * ``url``: URL of Jenkins instane
 * ``windows``: True if Jenkins runs on Windows

See the jenkins-cobertura plugin in the "contrib" directory for an example. The
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
There is a build-in generator for QtCreator project files.

A generator is called with 3 Arguments:

* ``package``: the :class:`bob.input.Package` to build the project for.
* ``argv``: Arguments not consumed by ``bob project``.
* ``extra``: Extra arguments to be passed back to ``bob dev`` when called from
  the IDE. These are the generic arguments that ``bob project`` parses for all
  generators.

A simple genertor may look like::

    def nullGenerator(package, argv, extra):
        return 0

    manifest = {
        'apiVersion' : "0.5",
        'projectGenerators' : {
            'nullGenerator' : nullGenerator,
        }
    }

