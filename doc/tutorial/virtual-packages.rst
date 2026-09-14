Alias packages
***************

.. highlight:: yaml

Bob has two, related but distinct, features that both go by the name
"alias":

* :ref:`Alias packages <configuration-aliases>` let you define a package
  name (e.g. ``toolchain``) that resolves to one of several interchangeable
  recipes (e.g. ``toolchain-linux`` or ``toolchain-windows``). This is
  commonly called a **virtual package**.
* The ``alias`` attribute of a :ref:`dependency
  <configuration-recipes-depends>` lets a single recipe depend on the *same*
  recipe more than once, under different names, typically to build several
  variants of it side by side.

This tutorial builds a small, self-contained example for each.

.. contents:: Table of contents
   :local:
   :depth: 1

----

Virtual packages
=================

Virtual packages are a common feature of package build systems to cope with
situations where multiple, interchangeable packages are available but only
one can be used at a time. Typical examples are libraries (e.g. ``libjpeg``
vs. ``jpeg-turbo``) or, as in :doc:`create`, a cross toolchain that comes as
a separate download for every host platform.

The mechanism is an :ref:`alias definition <configuration-aliases>`: a YAML
file in the ``aliases`` directory whose content is either a single string,
or a ``multiPackage`` dictionary that defines several related aliases at
once (e.g. a library and its ``-dev`` counterpart, all switching backend
together). Each such string is substituted by Bob just like any other
property (:ref:`configuration-principle-subst`) and used as the name of the
recipe that should actually be built. Everything downstream only ever sees
the alias name — which concrete recipe backs it is an implementation
detail.

Example project
-----------------

The following project defines a virtual ``message`` package. By default it
resolves to an English greeting, but the concrete backend can be swapped
with an environment variable::

    alias-demo/
    ├── aliases/
    │   └── message.yaml
    └── recipes/
        ├── greeter.yaml
        ├── message-english.yaml
        └── message-german.yaml

``recipes/message-english.yaml`` and ``recipes/message-german.yaml``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Two otherwise unrelated recipes that happen to produce a file with the same
name and the same purpose::

    # recipes/message-english.yaml
    packageScript: |
        echo "Hello!" > message.txt

::

    # recipes/message-german.yaml
    packageScript: |
        echo "Hallo!" > message.txt

``aliases/message.yaml``
~~~~~~~~~~~~~~~~~~~~~~~~~~

The virtual package itself. Its content is a plain string that picks the
backend recipe, falling back to the English one if ``MESSAGE_BACKEND`` is
unset::

    "${MESSAGE_BACKEND:-message-english}"

``recipes/greeter.yaml``
~~~~~~~~~~~~~~~~~~~~~~~~~~

The root package. It only ever depends on ``message`` — never on
``message-english`` or ``message-german`` directly::

    root: True

    depends:
        - message

    buildScript: |
        cp "$2/message.txt" .

    packageScript: |
        cp "$1/message.txt" .
        cat "$1/message.txt"

Building
---------

The package tree only shows the virtual package name, regardless of which
backend is actually used::

    $ bob ls -r
    greeter
    └── message

Build it without any override to get the default, English backend::

    $ bob build greeter -v
    ...
    Hello!
    Build result is in work/greeter/dist/1/workspace

Now override ``MESSAGE_BACKEND`` on the command line
(:ref:`manpage-build`'s ``-D`` option) to select the German backend
instead — no recipe was touched::

    $ bob build greeter -v -D MESSAGE_BACKEND=message-german
    ...
    Hallo!
    Build result is in work/greeter/dist/2/workspace

Note how the dist directory changed from ``1`` to ``2``: from Bob's point of
view this is simply a new variant of the (virtual) ``message`` package, not
a different package.

.. note::
    Unlike the ``multiPackage`` of recipes and classes, an alias's
    ``multiPackage`` does not support nesting — see
    :ref:`configuration-aliases` for the full syntax.

----

Aliased dependencies
======================

Sometimes you need the *opposite* of a virtual package: not "one name, many
possible recipes" but "one recipe, used several times with different
parameters" — for example to build a debug and a release variant of the same
library side by side. Because :ref:`configuration-recipes-depends` requires
every dependency to have a unique name, and that name defaults to the
recipe's name, depending on the same recipe twice would normally collide.
The dependency-level ``alias`` attribute solves this by giving each instance
of the dependency its own name.

Example project
-----------------

This project builds two variants of the same ``greeting`` recipe — one
English, one German — and combines both results into a single package::

    aliased-deps-demo/
    └── recipes/
        ├── greeting.yaml
        └── polyglot.yaml

``recipes/greeting.yaml``
~~~~~~~~~~~~~~~~~~~~~~~~~~~

A generic recipe that just writes whatever ``$GREETING`` it was given::

    packageVars: [GREETING]

    packageScript: |
        echo "${GREETING:-Hello, World!}" > greeting.txt

``recipes/polyglot.yaml``
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Depends on ``greeting`` twice. Each dependency gets its own ``alias`` name
and its own ``environment`` override for ``GREETING``::

    root: True

    depends:
        - name: greeting
          alias: greeting-en
          environment:
              GREETING: "Hello, World!"
        - name: greeting
          alias: greeting-de
          environment:
              GREETING: "Hallo, Welt!"

    buildScript: |
        cat "$2/greeting.txt" "$3/greeting.txt" > combined.txt

    packageScript: |
        cp "$1/combined.txt" .
        cat "$1/combined.txt"

How it works
-------------

``alias``
    Renames a dependency for the purpose of the unique-name requirement and
    for display (see ``bob ls -r`` below). Both dependencies still refer to
    the same recipe, ``greeting``.

``environment``
    Overrides ``$GREETING`` individually for each of the two dependency
    instances, before ``greeting`` is even evaluated. This is what actually
    makes the two instances build differently.

``$2`` / ``$3``
    Because ``polyglot`` has no ``checkoutSCM``, ``$1`` is reserved (and
    invalid) in ``buildScript``. The two dependencies are passed in the
    order they are declared, starting at ``$2``.

Building
---------

Both variants show up separately in the package tree, named after their
alias rather than after the shared recipe::

    $ bob ls -r
    polyglot
    ├── greeting-de
    └── greeting-en

Building the root package builds ``greeting`` twice — once per alias — and
combines both outputs::

    $ bob build polyglot -v
    >> polyglot/greeting-en
       PACKAGE   work/greeting/dist/1/workspace
    >> polyglot/greeting-de
       PACKAGE   work/greeting/dist/2/workspace
    >> polyglot
       BUILD     work/polyglot/build/1/workspace
       PACKAGE   work/polyglot/dist/1/workspace
    Hello, World!
    Hallo, Welt!
    Build result is in work/polyglot/dist/1/workspace

Both dependencies are built from the very same recipe file, yet Bob treats
them as two independent packages — ``work/greeting/dist/1`` and
``work/greeting/dist/2`` — because their environment, and thus their build
result, differs.
