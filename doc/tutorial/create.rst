Create a simple cross compiling project
***************************************

.. highlight:: yaml

This tutorial builds a small project from scratch that cross-compiles a
"Hello World" C program for **Arm AArch64 Linux**. It demonstrates three
things at once:

* All recipe scripts are written in **Python** instead of the default Bash
  (or PowerShell). This keeps the recipes identical on Linux and Windows —
  there is no need to maintain separate Bash and PowerShell variants of the
  same logic.
* The project works out of the box on both **Linux** and **Windows** hosts.
* Demonstrate the use of **virtual packages** (:ref:`alias <configuration-aliases>`)
  to select the right prebuilt Arm cross toolchain for the host you happen to
  build on, so the ``hello-world`` recipe itself does not need to know (or
  care) whether it runs on Linux or Windows.

.. contents:: Table of contents
   :local:
   :depth: 1

----

Prerequisites
=============

* Bob is installed and available in ``PATH``.
* A Python 3 interpreter is available in ``PATH``. Needed to run Bob and used
  by Bob itself to run the recipe scripts of this tutorial. (Windows: the
  ``python`` launcher from `python.org <https://www.python.org/downloads/windows/>`_
  or the Microsoft Store package both work.)
* Internet access to download the Arm GNU Toolchain (roughly 140 MB on Linux,
  300 MB on Windows).
* Optionally, `QEMU <https://www.qemu.org/>`_ user-mode emulation
  (``qemu-aarch64``) if you want to actually run the cross-compiled binary
  afterwards.

Project layout
==============

Create an empty directory for the project and add the following files:

.. code-block:: none

    my-project/
    ├── config.yaml
    ├── aliases/
    │   └── toolchain.yaml
    ├── recipes/
    │   ├── hello-world.yaml
    │   ├── toolchain-linux.yaml
    │   └── toolchain-windows.yaml
    └── src/
        └── hello.c

``config.yaml``
================

First, require a recent Bob version and switch the whole project to Python
as its scripting language::

    bobMinimumVersion: "1.2"
    scriptLanguage: python

Because ``scriptLanguage`` is set here, every plain ``checkoutScript``,
``buildScript`` and ``packageScript`` in this project is interpreted as a
Python script. A recipe could still override this with an explicit
``scriptLanguageBash``/``...Pwsh``/``...Python`` suffix, but this tutorial
does not need that.

``src/hello.c``
================

The actual program that gets cross-compiled. Straight from the textbook:

.. code-block:: c

    #include <stdio.h>

    int main(void)
    {
        printf("Hello World from AArch64!\n");
        return 0;
    }

The virtual toolchain package
==============================

The `Arm GNU Toolchain <https://developer.arm.com/downloads/-/arm-gnu-toolchain-downloads>`_
is published as prebuilt archives, one per *host* platform, all producing the
same ``aarch64-none-linux-gnu`` *target*. We package one recipe per host
variant and then use an :ref:`alias <configuration-aliases>` to expose them
under a single, host-independent package name: ``toolchain``.

``recipes/toolchain-linux.yaml``
---------------------------------

Downloads the toolchain that is hosted on x86_64 Linux::

    checkoutSCM:
        scm: url
        url: https://developer.arm.com/-/media/Files/downloads/gnu/13.3.rel1/binrel/arm-gnu-toolchain-13.3.rel1-x86_64-aarch64-none-linux-gnu.tar.xz
        digestSHA256: "322f0b4482fc0d9fa0bb468134841f08d8c554c54ff5aa29a13a7a24bf7e1eb5"
        stripComponents: 1

    buildScript: |
        import shutil
        shutil.copytree(sys.argv[1], ".", dirs_exist_ok=True)

    packageScript: |
        import shutil
        shutil.copytree(sys.argv[1], ".", dirs_exist_ok=True)

    provideTools:
        cross-gcc:
            path: bin
            environment:
                CC: aarch64-none-linux-gnu-gcc

``recipes/toolchain-windows.yaml``
------------------------------------

Downloads the toolchain that is hosted on Windows (``mingw-w64-i686``). It
packages the exact same ``aarch64-none-linux-gnu`` target compiler, just
built to run as a native Windows executable::

    checkoutSCM:
        scm: url
        url: https://developer.arm.com/-/media/Files/downloads/gnu/13.3.rel1/binrel/arm-gnu-toolchain-13.3.rel1-mingw-w64-i686-aarch64-none-linux-gnu.zip
        digestSHA256: "e5cc8d9a7f1970b7605d14431fd011e41d63d13ded61eb53f03b578a9343926c"

    buildScript: |
        import shutil
        # Unlike "stripComponents" for tar archives, Bob cannot strip the
        # leading directory of a zip file. It still contains a single
        # top-level directory, so descend into it manually.
        (entry,) = os.listdir(sys.argv[1])
        shutil.copytree(os.path.join(sys.argv[1], entry), ".", dirs_exist_ok=True)

    packageScript: |
        import shutil
        shutil.copytree(sys.argv[1], ".", dirs_exist_ok=True)

    provideTools:
        cross-gcc:
            path: bin
            environment:
                CC: aarch64-none-linux-gnu-gcc

.. note::
    Obtain the ``digestSHA256`` values with ``sha256sum <file>`` (Linux) or
    ``(Get-FileHash <file> -Algorithm SHA256).Hash.ToLower()`` (PowerShell)
    after downloading the archives once. You should also check the
    signatures published alongside the archives to rule out any tampering of
    your initial download.

How the recipes work
~~~~~~~~~~~~~~~~~~~~~

``checkoutSCM``
    Downloads the archive. The ``url`` SCM extracts ``.tar.xz`` and ``.zip``
    files automatically. ``stripComponents: 1`` removes the leading
    ``arm-gnu-toolchain-...`` directory from the *tar* archive — this option
    only exists for tar files, which is why the Windows recipe has to do the
    equivalent by hand in ``buildScript``.

``buildScript``
    Copies the (now flat) toolchain tree from the checkout workspace
    (``sys.argv[1]``) into the build workspace. Using Python's ``shutil``
    here means the *exact same kind of code* works for both the Linux and
    the Windows recipe — no ``cp -r`` vs. ``Copy-Item -Recurse`` split is
    needed.

``packageScript``
    Copies the build workspace into the package workspace, which becomes the
    final, cacheable build result of the recipe.

``provideTools``
    Declares a tool named ``cross-gcc``. Its ``path: bin`` is added to
    ``$PATH`` of any recipe that consumes it, and its ``environment`` entry
    exposes the compiler name as ``$CC``/``os.environ["CC"]`` — always
    ``aarch64-none-linux-gnu-gcc``, without a ``.exe`` suffix, even on
    Windows. This works because Windows itself appends ``.exe`` when
    searching ``$PATH`` for an executable that has no extension, so the
    consuming recipe does not need to special-case the host platform at all.

    Both recipes provide the **same tool name** with the **same environment
    variable**. This is the key to making them interchangeable: whichever of
    the two recipes ends up being used, downstream recipes see an identical
    ``cross-gcc`` tool.

.. important::
    A C/C++ toolchain must never be consumed as a *weak* tool
    (:ref:`configuration-recipes-tools`). Its exact version and target
    directly influence the build result, so this tutorial always uses
    ``buildTools``/``packageTools``, never the weak variants.

``aliases/toolchain.yaml``
----------------------------

This is the actual virtual package. It resolves the plain name ``toolchain``
to one of the two concrete recipes above, depending on the host Bob runs
on::

    "$(if-then-else,$(eq,${BOB_HOST_PLATFORM},linux),toolchain-linux,toolchain-windows)"

``BOB_HOST_PLATFORM`` is a built-in variable that Bob always sets to one of
``linux``, ``msys``, ``cygwin``, ``win32`` or ``darwin``
(:ref:`configuration-recipes-vars`). The condition only singles out
``linux``; every Windows variant (native ``win32``, MSYS2, Cygwin) falls
through to ``toolchain-windows``. See :ref:`configuration-principle-subst`
for the ``$(eq,...)``/``$(if-then-else,...)`` string functions used here.

Because the alias file is just a plain string, Bob substitutes it exactly
like any other string property. Any recipe that depends on ``toolchain``
never sees ``toolchain-linux`` or ``toolchain-windows`` — as far as the rest
of the project and the ``bob ls -r`` package tree are concerned, there is
only one package: ``toolchain``.

The ``hello-world`` recipe
============================

``recipes/hello-world.yaml``
------------------------------

The consumer of the virtual toolchain package. It checks out ``src/``,
compiles ``hello.c`` for AArch64 and packages the resulting binary::

    root: True

    checkoutSCM:
        scm: import
        url: src

    depends:
        - name: toolchain
          use: [tools]

    buildTools: [cross-gcc]
    buildVars: [CC]

    buildScript: |
        import subprocess
        subprocess.check_call([
            os.environ["CC"], "-static", "-o", "hello",
            os.path.join(sys.argv[1], "hello.c"),
        ])

    packageScript: |
        import shutil
        os.makedirs("bin", exist_ok=True)
        shutil.copy(os.path.join(sys.argv[1], "hello"), "bin/hello")

How the recipe works
~~~~~~~~~~~~~~~~~~~~~

``checkoutSCM: scm: import``
    Copies the ``src`` directory of the project (relative to the project
    root) into the checkout workspace. This is a convenient way to keep a
    small, project-local source tree without setting up a real SCM for it.

``depends: - name: toolchain, use: [tools]``
    Depends on the virtual ``toolchain`` package but only imports its
    declared tools — not its build result. This is exactly the same
    dependency declaration regardless of which concrete recipe the
    ``toolchain`` alias resolves to.

``buildTools: [cross-gcc]`` / ``buildVars: [CC]``
    Makes the ``cross-gcc`` tool (and thus ``aarch64-none-linux-gnu-gcc`` on
    ``$PATH``) and the ``$CC`` environment variable it provides available to
    ``buildScript``.

``buildScript``
    Runs the cross compiler on ``hello.c`` from the checkout workspace
    (``sys.argv[1]``). ``-static`` links against the toolchain's bundled
    static libc, so the resulting binary has no runtime dependency on target
    libraries — handy for quickly testing it with QEMU.

``packageScript``
    Copies the compiled binary from the build workspace (``sys.argv[1]``)
    into ``bin/`` of the package result.

Building
========

From inside the project directory, first look at the package tree:

.. code-block:: none

    $ bob ls -r
    hello-world
    └── toolchain

Note that the tree looks identical on Linux and Windows — that is the whole
point of the virtual package. Now build it:

.. code-block:: none

    $ bob build hello-world -v

Bob will:

1. Download and unpack the Arm GNU Toolchain that matches your host
   (checkout/build/package steps of ``toolchain-linux`` or
   ``toolchain-windows``, picked transparently through the ``toolchain``
   alias).
2. Copy ``src/hello.c`` into the checkout workspace of ``hello-world``.
3. Cross-compile it with the packaged ``aarch64-none-linux-gnu-gcc``.
4. Package the resulting ``hello`` binary.

The output looks similar to this on Linux (Windows only differs in the path
separators):

.. code-block:: none

    >> hello-world/toolchain
       CHECKOUT  work/toolchain/src/1/workspace (initial checkout)
       BUILD     work/toolchain/build/1/workspace
       PACKAGE   work/toolchain/dist/1/workspace
    >> hello-world
       CHECKOUT  work/hello-world/src/1/workspace (initial checkout)
       BUILD     work/hello-world/build/1/workspace
       PACKAGE   work/hello-world/dist/1/workspace
    Build result is in work/hello-world/dist/1/workspace

.. note::
   If you experience an SSL error on Windows when the toolchain is downloaded
   (e.g. *"Error: Failed to download: <urlopen error [SSL:
   CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local
   issuer certificate (_ssl.c:1082)>"*), installing `pip-system-certs
   <https://pypi.org/project/pip-system-certs/>`_ may help.

Verifying the result
=====================

The package result contains an AArch64 ELF binary, not something your build
host can execute directly. You can check its architecture with ``file``
(Linux/WSL):

.. code-block:: none

    $ file work/hello-world/dist/1/workspace/bin/hello
    work/hello-world/dist/1/workspace/bin/hello: ELF 64-bit LSB executable,
    ARM aarch64, ..., statically linked, ...

If you have QEMU's user-mode emulation installed, you can even run it:

.. code-block:: none

    $ qemu-aarch64 work/hello-world/dist/1/workspace/bin/hello
    Hello World from AArch64!

Extending to more host platforms
==================================

Adding support for another host, e.g. macOS, only takes two steps and never
touches ``hello-world.yaml``:

1. Add a ``recipes/toolchain-darwin.yaml`` recipe that downloads the
   matching Arm GNU Toolchain archive and provides the same ``cross-gcc``
   tool with the same ``CC`` variable.
2. Extend ``aliases/toolchain.yaml`` with another branch, e.g.::

       "$(if-then-else,$(eq,${BOB_HOST_PLATFORM},linux),toolchain-linux,$(if-then-else,$(eq,${BOB_HOST_PLATFORM},darwin),toolchain-darwin,toolchain-windows))"

This is the general pattern for virtual packages: keep the *interface*
(tool names, provided environment variables) identical across all variants
and let a single alias pick the concrete implementation.
