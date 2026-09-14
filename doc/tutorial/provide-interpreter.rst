.. highlight:: yaml

Providing a Script Interpreter via a Recipe
********************************************

By default, Bob runs scripts using the interpreter found in the system's
``$PATH``. This means the build result may vary between machines that have
different versions of the interpreter installed. The
:ref:`configuration-recipes-provideInterpreters` keyword solves this by letting
one recipe supply the interpreter executable that another recipe's scripts will
be run with. The provider packages the interpreter as its build result and
advertises it; the consumer declares ``interpreters`` in the ``use`` list of
that dependency to pick it up.

This tutorial covers two concrete examples:

1. **Windows** — Download the official Python embeddable package, expose
   ``python.exe`` via ``provideInterpreters`` and run a small Python script that
   reports its own interpreter path.
2. **Linux** — Build Bash from source, expose the resulting ``bash`` binary via
   ``provideInterpreters`` and run a Bash script that reports its own interpreter
   path.

Both examples follow the same pattern: a *provider* recipe packages the
interpreter and a *consumer* recipe uses it.

.. contents:: Table of contents
   :local:
   :depth: 1

----

Windows example: providing a Python interpreter
================================================

Prerequisites
-------------

* Bob is installed and available in ``PATH``.
* The build machine runs Windows with PowerShell available.
* Internet access to download the Python embeddable zip.

Project layout
--------------

Create an empty directory for the project and add the following files:

.. code-block:: none

    my-project/
    ├── config.yaml
    └── recipes/
        ├── python-interpreter.yaml
        └── hello-world.yaml

``config.yaml``
---------------

First of all, a recent version of Bob should be required by the project to use
the latest features. Because most scripts in a typical Windows project are
written in PowerShell, set it as the project-wide default scripting language::

    bobMinimumVersion: "1.2"
    scriptLanguage: PowerShell

Recipes that use an explicit language suffix (``ScriptPwsh``, ``ScriptPython``,
…) always override this setting, so the value in ``config.yaml`` only acts as
a fallback for unsuffixed ``Script`` keywords.

The ``python-interpreter`` recipe
----------------------------------

This recipe downloads the Python embeddable zip, extracts it and makes
``python.exe`` available as a Python interpreter for downstream recipes.

Create ``recipes/python-interpreter.yaml``::

    checkoutSCM:
        scm: url
        url: https://www.python.org/ftp/python/3.14.7/python-3.14.7-embed-amd64.zip
        digestSHA256: "d297e5ff019966817ad8502465176139f2d3d840fa4ed84b13bed399a6ab1f15"
        extract: False

    buildScriptPwsh: |
        # $args[0] is the checkout workspace that holds the downloaded zip.
        $zip = Join-Path $args[0] "python-3.14.7-embed-amd64.zip"
        Copy-Item -Path $zip -Destination "." -Force

    packageScriptPwsh: |
        # $args[0] is the build workspace that holds the copied zip.
        $zip = Join-Path $args[0] "python-3.14.7-embed-amd64.zip"
        Expand-Archive -Path $zip -DestinationPath "." -Force

    provideInterpreters:
        python: python.exe

.. note::
    The ``digestSHA256`` field pins the downloaded file to a known-good
    version and protects against accidental changes or tampered mirrors. To
    obtain the hash, download the zip once and run::

        (Get-FileHash python-3.13.2-embed-amd64.zip -Algorithm SHA256).Hash.ToLower()

    You should probably check signatures that are present alongside the archive
    on the server to rule out any tampering of your initial download.

How the recipe works
~~~~~~~~~~~~~~~~~~~~

``checkoutSCM``
    The ``url`` SCM downloads the zip into the checkout workspace. The optional
    ``extract: False`` setting prevents Bob from automatically extracting the
    archive.

``buildScriptPwsh``
    Copies the zip file from the checkout workspace (``$args[0]``) into the
    build workspace. Because there is nothing to be built, this is all the
    step has to do.

``packageScriptPwsh``
    PowerShell's ``Expand-Archive`` unpacks the zip into the package workspace,
    which becomes the package result. ``$args[0]`` is the build workspace passed
    by Bob as the first argument to every package script.

``provideInterpreters``
    Declares that the ``python`` scripting language should be driven by
    ``python.exe`` found at the root of this recipe's package result.
    Downstream recipes that list ``interpreters`` in their ``use`` clause will
    use this exact executable instead of the system Python.

The ``hello-world`` recipe
--------------------------

This recipe has no sources of its own.  Its sole job is to run a small Python
script that prints ``"Hello World"`` and the path of the Python interpreter
that executed it.

Create ``recipes/hello-world.yaml``::

    root: True
    scriptLanguage: python

    depends:
        - name: python-interpreter
          use: [interpreters]

    packageScriptPython: |
        import sys
        print("Hello World")
        print(f"Python interpreter: {sys.executable}")

How the recipe works
~~~~~~~~~~~~~~~~~~~~

``scriptLanguage: python``
    Overrides the default of ``config.yaml`` regarding the script language.  It
    makes this (and only this!) recipe use Python as scripting language.

``use: [interpreters]``
    Picks up the ``provideInterpreters`` declaration from ``python-interpreter``
    and makes it effective for all scripts in this recipe.

``packageScriptPython``
    Bob executes this script with the ``python`` interpreter.  Because
    ``python-interpreter`` supplied that interpreter, ``sys.executable`` will
    point into the package result of ``python-interpreter`` rather than to the
    system Python.

If ``forward: True`` were added to the dependency, the interpreter would also
be propagated to every recipe listed *after* ``python-interpreter`` in the same
``depends`` block — useful when an entire subtree of recipes should share the
same Python installation.

Building
--------

Run Bob from inside the project directory:

.. code-block:: none

    C:\my-project> bob build hello-world -v

Bob will:

1. Download the Python embeddable zip (checkout step of ``python-interpreter``).
2. Extract it (build step of ``python-interpreter``).
3. Assemble the package (package step of ``python-interpreter``).
4. Run the Python script with the packaged interpreter (package step of
   ``hello-world``).

The output of the build should look similar to:

.. code-block:: none

    >> hello-world/python-interpreter
       CHECKOUT  work\python-interpreter\src\1\workspace (initial checkout)
       AUDIT     work\python-interpreter\src\1\workspace .. ok
       BUILD     work\python-interpreter\build\1\workspace
       AUDIT     work\python-interpreter\build\1\workspace .. ok
       PACKAGE   work\python-interpreter\dist\1\workspace
       AUDIT     work\python-interpreter\dist\1\workspace .. ok
    >> hello-world
       PACKAGE   work\hello-world\dist\1\workspace
    Hello World
    Python interpreter: C:\my-project\work\python-interpreter\dist\1\workspace\python.exe
       AUDIT     work\hello-world\dist\1\workspace .. ok
    Build result is in work\hello-world\dist\1\workspace
    Duration: 0:00:03.874190, 1 checkout (0 overrides active), 2 packages built, 0 downloaded.

The exact path varies with the Bob workspace layout, but it will always point
into the ``python-interpreter`` result directory — confirming that the
downloaded Python was used instead of any system installation.

.. note::
    The Python embeddable distribution is intentionally minimal.  It contains
    the interpreter and the standard library in compressed form but does *not*
    include ``pip`` or many optional modules. For build tasks that require
    additional packages, consider using the full Python package instead of the
    embeddable variant.

----

Linux example: building Bash from source
=========================================

This example goes one step further: the interpreter is not simply downloaded as
a pre-built binary but *compiled from source* as part of the Bob build.  Any
recipe in the same project can then use this hermetic Bash build for its
scripts, independent of the version installed on the host.

Prerequisites
-------------

* Bob is installed and available in ``PATH``.
* The build machine runs Linux with a C compiler and the usual autotools
  prerequisites (``gcc``, ``make``, ``tar``).
* Internet access to download the Bash source tarball.

Project layout
--------------


.. code-block:: none

    my-project/
    ├── config.yaml
    └── recipes/
        ├── bash-interpreter.yaml
        └── hello-world-bash.yaml

``config.yaml``
---------------

On Linux, Bash is the natural default scripting language::

    scriptLanguage: bash

This is actually the Bob default as well, so the file can be omitted
entirely. It is listed here for clarity.

The ``bash-interpreter`` recipe
--------------------------------

This recipe downloads the Bash 5.2 source tarball, compiles it and packages
the resulting ``bash`` binary.

Create ``recipes/bash-interpreter.yaml``::

    checkoutSCM:
        scm: url
        url: https://ftp.gnu.org/gnu/bash/bash-5.3.tar.gz
        digestSHA256: "0d5cd86965f869a26cf64f4b71be7b96f90a3ba8b3d74e27e8e9d9d5550f31ba"
        stripComponents: 1

    buildScript: |
        # $1 is the checkout workspace that holds the downloaded, extracted tarball.
        "$1/configure" --without-bash-malloc
        make -j"$(nproc)"

    packageScript: |
        # $1 is the build workspace.  Copy the compiled binary into bin/.
        mkdir -p bin
        cp "$1/bash" bin/

    provideInterpreters:
        bash: bin/bash

.. note::
    Obtain the ``digestSHA256`` value with::

        sha256sum bash-5.3.tar.gz

    You should probably check signatures that are present alongside the archive
    on the server to rule out any tampering of your initial download.

How the recipe works
~~~~~~~~~~~~~~~~~~~~

``checkoutSCM``
    Downloads ``bash-5.3.tar.gz`` into the checkout workspace and extract it
    automatically. Strips the first path element (``bash-5.3``) via
    ``stripComponents`` to keep the build script version independent.

``buildScript``
    Bob passes the checkout workspace as ``$1``.  The script runs ``configure``
    directly from the checkout workspace and compiles Bash out of source tree.
    The flag ``--without-bash-malloc`` disables Bash's private memory
    allocator, which is not needed here and simplifies the build slightly.  The
    compiled ``bash`` binary is left inside the build tree within the build
    workspace.

``packageScript``
    Copies the compiled binary into ``bin/`` of the package workspace. The
    package workspace becomes the build result that Bob stores and can cache.

``provideInterpreters``
    Declares that the ``bash`` scripting language should be driven by the
    ``bash`` binary at ``bin/bash`` relative to this recipe's package result.

The ``hello-world-bash`` recipe
--------------------------------

Create ``recipes/hello-world-bash.yaml``::

    root: True

    depends:
        - name: bash-interpreter
          use: [interpreters]

    packageScript: |
        echo "Hello World"
        echo "Bash interpreter: $BASH"

How the recipe works
~~~~~~~~~~~~~~~~~~~~

``use: [interpreters]``
    Picks up the ``provideInterpreters`` declaration from ``bash-interpreter``
    and routes all Bash scripts in this recipe through the packaged
    ``bin/bash`` binary.

``packageScript``
    Bob executes this script with the provided Bash binary.  Inside the
    script, the special variable ``$BASH`` always expands to the full path of
    the Bash executable that is running the script, so it will point into the
    ``bash-interpreter`` package result rather than the system Bash.

Building
--------

.. code-block:: none

    $ cd my-project
    $ bob build hello-world-bash -v

Bob will:

1. Download the Bash source tarball (checkout step of ``bash-interpreter``).
2. Configure and compile Bash (build step of ``bash-interpreter``).
3. Package the binary (package step of ``bash-interpreter``).
4. Run the package script of ``hello-world-bash`` with the packaged Bash.

The output of the package step will look similar to:

.. code-block:: none

    Hello World
    Bash interpreter: /home/user/my-project/work/bash-interpreter/dist/1/workspace/bin/bash

The path will always point into the ``bash-interpreter`` workspace, confirming
that the compiled Bash — not the host's ``/bin/bash`` — executed the script.
