.. highlight:: json

.. _audit-trail:

Audit trail
===========

For every build artifact Bob records all involved sources and build steps that
lead to a particular artifact. The recorded information contains at least, but
not limited to, the following records:

* state of the recipes,
* recipe name
* package path,
* build host/time,
* environment,
* dependencies (with their respective audit trail),
* state of SCMs (e.g. commit id, dirty, ...).

The information in the audit trail records may be extended with additional
information in the future. An application that parses the audit trail should
ignore unknown fields for future compatibility.

Storage format
--------------

Audit trails are stored as gzip compressed JSON documents. For local builds the
audit trail is stored as ``audit.json.gz`` file next to the workspace. Jenkins
builds only rely on binary artifacts where the same file is stored in the
compressed tar file in a ``meta/`` directory. The general structure of an audit
trail looks like the following:

.. code-block:: yaml

    {
        "artifact" : {
            // audit record
        },
        "references" : [
            {
                // audit record
            },
            ...
        ]
    }

The audit information about the involved artifact is stored under the
``artifact`` key. Any audit records about the dependencies that were used to
create the artifact are included in the list under the ``references`` key. This
includes all transitive records too. A correct audit trail must include the
full transitive information to be accepted by Bob.

Records
-------

The following sections describe the various keys and their semantics that can
be found in an audit record.

Example of a single audit record::

    {
      "artifact-id" : "c1cd9616caa783fcd8ef9b170cd968ccb306a727",
      "variant-id" : "f5aa3695a2d6f0e70af2ffaf43bb461a428e6fba",
      "build-id" : "a95ce8e3e30b7535751cefea942941c31a8ad1aa",
      "result-hash" : "7710368d8165f1c780fb9f33b34415ab76a618c0",
      "env" : "declare -- BASH=\"/bin/bash\"\ndeclare -r BASHOPTS=...",
      "metaEnv" : {
         "VERSION" : "1.2.3",
         "LICENSE" : "GPLv2"
      },
      "scms" : [],
      "dependencies" : {
         "args" : [
            "c5b2a8231156f43728af34f3a2dcb731ade2f76a"
         ]
      },
      "meta" : {
         "language" : "bash",
         "recipe" : "root",
         "step" : "dist",
         "bob" : "0.12.1",
         "package" : "root"
      },
      "build" : {
         "date" : "2019-12-02T13:19:34.193136+00:00",
         "machine" : "x86_64",
         "nodename" : "kloetzke",
         "os-release" : "PRETTY_NAME=\"Debian GNU/Linux 10 (buster)\"\nNAME=...",
         "release" : "4.19.0-6-amd64",
         "sysname" : "Linux",
         "version" : "#1 SMP Debian 4.19.67-2+deb10u2 (2019-11-11)"
      }
    }

Basic information
~~~~~~~~~~~~~~~~~

``artifact-id``
    Hexadecimal number that identifies a particular artifact. This is also the
    primary key for audit records.

``variant-id``
    The Variant-Id as described in :ref:`concepts-implicit-versioning`.

``build-id``
    The Build-Id as described in :ref:`concepts-implicit-versioning`.

``result-hash``
    A hash sum across the content of the workspace after the artifact was
    built.

``env``
    Dump of the bash environment as created by ``declare -p``. See
    `bash declare`_. For PowerShell recipes it is a JSON string that contains
    all internal variables and environment variables as dictionaries. Use
    the ``meta.language`` key to determine the used scripting language.

``metaEnv``
    This is a dictionary of all :ref:`configuration-recipes-metaenv` variables
    of the package. They are included in the audit trail regardless of their
    actual usage.

.. _bash declare: https://www.gnu.org/software/bash/manual/html_node/Bash-Builtins.html#index-declare

Recipes
~~~~~~~

If Bob recognizes that the recipes are managed in a supported SCM (currently
git or svn) there will be a ``recipes`` key in the audit record. The format of
the object under this key is described in :ref:`audit-trail-scms`.


Dependencies
~~~~~~~~~~~~

Each step can have any number of dependencies. They will be recorded under a
``dependencies`` key. The other step is referenced by the Artifact-Id and their
audit record will be found in the ``references`` list of the audit trail. There
are three types of dependencies to other steps that each have their different
representation in audit record:

``arguments``
    Ordered list of all dependencies whose result was input to this step. They
    correspond to the ``$1`` to ``$n`` arguments of the script that was
    executed.

``tools``
    Object that maps all available tools by their name to the Artifact-Id.

``sandbox``
    Used sandbox during execution.

Example::

    "dependencies" : {
        "args" : [
            "b0a6632c6e7677220e46e4ae9c528efb949137c6"
        ],
        "tools" : {
            "toolchain" : "0b1c5e3489bed347ccf8e0e1e12dc70c92b09472"
        },
        "sandbox" : "3473b28df3891046618420428b530418ce006ad9"
    }

.. _audit-trail-scms:

SCMs
~~~~

All SCMs are recorded after the checkout step was run. The audit record will
contain a list of objects under the ``scms`` key. Each object has at least a
``type`` key that identifies the kind of SCM and a ``dir`` key for the relative
directory (or file) that was managed by the SCM in the workspace.

See the following list for the additional information that each SCM adds to the
record:

git
    The git SCM records all remotes, the current commit that HEAD points to and
    if the tree is dirty. The output of ``git describe`` is also recorded.

    Example::

        {
            "commit": "6e986014563b70ecd867fb6a6e1adeb408f63dd6",
            "description": "v0.11.0-59-g6e98601-dirty",
            "dir": ".",
            "dirty": true
            "remotes": {
                "origin": "git@github.com:BobBuildTool/bob.git"
            },
            "type": "git",
        }

svn
    Example::

        {
            "dir" : ".",
            "dirty" : false,
            "repository" : {
                "root" : "http://svn.haiku-os.org/oldhaiku",
                "uuid" : "a95241bf-73f2-0310-859d-f6bbb57e9c96",
            },
            "revision" : 43238,
            "type" : "svn",
            "url" : "http://svn.haiku-os.org/oldhaiku/haiku/",
        }

url
    Example::

        {
            "digest" : {
                "algorithm" : "sha1",
                "value" : "697b7c87c73eb53bf80e19b65a4ac245214d530c" 
            },
            "dir" : "author.txt",
            "type" : "url",
            "url" : "https://example.test/author.txt",
        }


Meta data
~~~~~~~~~

There can be any number of key-value meta data pairs. They will be contained
under the ``meta`` key and typically hold at least the following information:

``bob``
    Bob version string.

``language``
   The scripting language that was used to create the artifact. Can be ``bash``
   or ``PowerShell``. If missing it must be interpreted as ``bash``. Use this to
   correctly parse the ``env`` string.

``package``
    Package path of the artifact that was built. Note that there might be
    multiple packages that produce the same result. Only one will be built by
    Bob without recording all possible package paths here.

``recipe``
    Name of the recipe that declared the package.

``step``
    The executed step for this audit record. Can be ``src``, ``build`` or
    ``dist``.

If the artifact was built on Jenkins the following additional information will
be included:

``jenkins-build-tag``
   The Jenkins build tag (``jenkins-${JOB_NAME}-${BUILD_NUMBER}``) as set in
   ``$BUILD_TAG``.

``jenkins-node``
   The name of the node where the artifact had been built. Equals 'master' for
   master node. Taken over from ``$NODE_NAME``.

``jenkins-build-url``
   The URL where the results of the Jenkins  build can be found (``$BUILD_URL``).


Example::

    "meta" : {
        "bob" : "0.11.0-56-g9b3d2c6-dirty",
        "package" : "root/lib"
        "recipe" : "lib",
        "step" : "src",
    },

Build data
~~~~~~~~~~

The build data describes when and where the artifact has been built. It can be
found under the ``build`` key and contains the following fields:

``date``
    The date and time of the build. This is stored as UTC time and formatted in
    ISO 8601 format with full precision.

``machine``
    The hardware identifier as returned by the uname system call. This is
    typically the processor architecture of the host.

``nodename``
    The host name.

``os-release``
    This optional field holds the content of ``/etc/os-release``, if existing.
    If the file does not exist or cannot be read then this field will not be
    present.

``release``
    The operating system release.

``sysname``
    The operating system name (e.g. "Linux").

``version``
    The operating system version.

.. attention::
   The information of the ``machine``, ``release``, ``sysname``, ``version``
   and possibly ``nodename`` fields show the host in case of container builds,
   e.g. when running in a docker container. Be careful when relying on this
   information. The ``os-release`` field, if present, is more reliable in this
   case.

