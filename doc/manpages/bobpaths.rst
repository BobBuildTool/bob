.. _manpage-bobpaths:

bobpaths
========

.. only:: not man

   Name
   ----

   bobpaths - Specifying paths to Bob packages

Description
-----------

Most Bob commands are working on sets of packages. They can be specified by a
query language that loosely resembles Unix paths for the common case and XPath
for more advanced features. In contrast to these, Bob path queries are working
on general directed acyclic graphs instead of trees. Additionally
:ref:`alias substitution <manpage-bobpaths-aliases>` is supported to abbreviate
often used paths.

Examples:

* ``/foo/bar`` selects the ``bar`` package under the ``foo`` top level package
* ``//*-unittests`` selects all packages that end with ``-unittests``
* ``/image//*[ "${LICENSE}" == "GPL" ]`` selects all GPL licensed packages that
  are descendants of the ``image`` top level package

When Bob parses the recipes he builds an internal package graph. The general
dependency structure is derived from the recipes. Depending on the actual
content one or more packages are generated from a recipe. The Bob queries are
working on the package graph.

The primary constructs of Bob paths are the location path and predicate
expressions. Both are evaluated with respect to a context which consists of:

* a package,
* a set of environment variables from the context package,
* and a string function library.

The environment variables in the context are derived from the context package.
Only variables that are explicitly consumed by the recipe (via
:ref:`{checkout,build,package}Vars <configuration-recipes-vars>`) and
:ref:`metaEnvironment <configuration-recipes-metaenv>` variables are available.
References to unset variables will result in an empty string.

The string function library is populated by all built-in string functions
and additional ones defined by :ref:`plugins <extending-hooks-string>`. The string
functions, if evaluated during the query, will get an additional ``package``
parameter which holds the current context package. The string function library
stays constant throughout the whole evaluation.

A path is parsed by first dividing the character string into tokens and then
parsing the resulting sequence of tokens. Whitespaces are ignored between
tokens and may be freely injected. Some tokes (e.g. ``*``,  ``[`` or ``]``)
collide with special characters of the shell. Care should be taken to correctly
quote or escape these characters when invoking Bob from the command line.

.. _manpage-bobpaths-locationpath:

Location path
-------------

Just like in Unix, location paths can be expressed using a straightforward
syntax. Similar to XPath this is actually a syntactic abbreviation of the more
verbose syntax which will be explained later. A location path selects a set of
packages relative to the context package. The result of evaluating a location
step is a set of contexts with packages that matched the axis, name test and
predicate of the location step. Following steps in the location path are then
recursively applied to the generated contexts.

Before diving into a formal definition here are some simple location path
examples:

* ``foo`` selects the ``foo`` child package of the context package
* ``f*`` selects all children of the context package starting with ``f``
* ``/`` selects the virtual root package that is parent to all top level
  packages (i.e. packages of recipes where ``root`` is true)
* ``/foo`` selects the ``foo`` top level package
* ``/foo/bar`` selects the ``bar`` child of the ``foo`` top level package

All examples above are abbreviations of the verbose syntax. See the following
examples for the full syntax:

* ``child@foo`` selects the ``foo`` child package of the context package
* ``chils@f*`` selects all children of the context package starting with ``f``
* ``/child@foo/child@bar`` selects the ``bar`` child of the ``foo`` top level
  package
* ``descendant@foo`` selects the ``foo`` descendants of the context package
* ``descendant-or-self@foo`` selects the ``foo`` descendants of the context
  package and, if the context package is named ``foo``, the context package as
  well
* ``self@foo`` selects the context package if it is named ``foo``, and
  otherwise selects nothing
* ``child@foo/descendant@bar`` selects the ``bar`` descendants of the ``foo``
  child of the context package
* ``child@*/child@foo`` selects all ``foo`` grandchildren of the context
  package
* ``child@*["${LICENSE}" == "GPLv2"]`` selects all children of the context package
  that are licensed as GPLv2
* ``child@lib*[child@libc]`` selects the children starting with ``lib`` of the
  context package that have a ``libc`` child (i.e. that have a dependency to
  ``libc``)
* ``descendant-or-self@lib*["${LICENSE}" == "GPLv2" && child@libc]`` selects
  the context package or any of it descendants that start with ``lib`` which
  are licensed as GPLv2 and have a direct dependency to ``libc``

There are two kinds of location path: relative location paths and absolute
location paths.

A relative location path consists of a sequence of one or more location steps
separated by /. The steps in a relative location path are composed together
from left to right. Each step in turn selects a set of packages relative to a
context package. An initial sequence of steps is composed together with a
following step as follows. The initial sequence of steps selects a set of
packages relative to a context package. Each package in that set is used as a
context package for the following step. The sets of packages identified by that
step are unioned together. The set of packages identified by the composition of
the steps is this union.

An absolute location path consists of / optionally followed by a relative
location path. A / by itself selects the virtual root package as context
package. If it is followed by a relative location path, then the location path
resolution starts with the virtual root package as input for the initial step.

Location steps
    A location step has three parts:

    * an axis, which specifies the graph relationship between the context
      package and the packages selected by the location step
    * a package name test, which filters the packages selected by the axis
      by their name
    * an optional predicate, which uses an arbitrary expression to further
      refine the set of packages that passed the package name test

    The syntax for a location step is ``axis@name[predicate]``.

Axis specifier
    The following axis are available:

    * the ``self`` axis contains just the context package itself,
    * the ``child`` axis contains all children of the context package,
    * the ``direct-child`` axis contains the direct children of the context
      package (i.e. without provided dependencies),
    * the ``descendant`` axis contains all descendants of the context package;
      a descendant is a child or a child of a child and so on,
    * the ``direct-descendant`` axis contains the direct descendants of the
      context package; a direct descendant is a direct child or a direct child
      of a direct child and so on,
    * the ``descendant-or-self`` axis contains the context package and the
      descendants of the context package
    * the ``direct-descendant-or-self`` axis contains the context package and
      the direct descendants of the context package.

Package name test
    For every package that is reachable by the axis the package name is matched
    with the package name test. Names must match exactly as given in the test.
    The special ``*`` wildcard character matches zero or more characters.

Predicates
    The predicate expression further filters the package set that was generated
    by the axis and passed the package name test. For each package in the
    package-set to be filtered, the expression is evaluated with that package
    as the context package. If the expression evaluates to true for that
    package, the package is included in the new package-set; otherwise, it is
    not included.

    If the result of the expression is string, the result will be converted to
    a boolean. The empty string, ``0`` and ``false`` (case insensitive) are
    treated as false. Any other string is converted to true.

Abbreviated Syntax
    The following abbreviations are available:

    * the ``child`` axis is implicitly assumed if no axis is specified. I.e.
      ``foo`` is equivalent to ``child@foo``.
    * ``.`` is a short-hand for ``self@*``
    * ``//`` is short for ``/descendant-or-self@*/``. For example, ``//foo`` is
      short for ``/descendant-or-self@*/child@foo`` and so will select any
      ``foo`` package in the package graph; ``foo//bar`` is short for
      ``child@foo/descendant-or-self@*/child@bar`` and so will select all
      ``bar`` descendants of ``foo`` children.
    * the above two short-cuts can be combined as ``.//foo`` which is
      equivalent to ``descendant@foo``

Predicate expressions
---------------------

Predicate expressions are evaluated as boolean functions that yield either true
or false. The expression is executed for a context package. If the expressions
yields true the package is kept as result of the associated location path,
otherwise the package is filtered.

An expression may combine the following primitives to arbitrarily complex
expressions. Several operators are available. Their associativity may be
overruled by using parenthesis. Each primitive may be of only one of the
following two types: string or boolean.  Depending on the context a (partial)
expression of string type may be implicitly converted to a boolean value. The
empty string, ``0`` and ``false`` (case insensitive) are treated as false. Any
other string is converted to true.

Location paths
~~~~~~~~~~~~~~
    Relative location paths are evaluated with respect to the context package
    of the predicate expression. Absolute location paths are evaluated
    independent of that. If the location path yields an empty set of packages
    the boolean result is false. If one or more packages are matched by the
    location path the result is treated as true.

    Semantically this represents an *exists* predicate. As the location path is
    evaluated with respect to the current context of the expression the
    location path means "there exists a path from the current context package
    matched by the location path". By this primitive arbitrary graph
    reachability relations may be expressed.

.. _bobpaths_string_literals:

String literals
~~~~~~~~~~~~~~~
    Strings consist of a sequence of zero or more characters enclosed in double
    quotes (``"``). Strings are subject to the same
    :ref:`string substitution <configuration-principle-subst>` as in the
    recipes. Unset variables are expanded to empty strings and are not treated
    as errors. The available variables are defined by the context of the whole
    expression.

    To include double quotes as character into the string it has to be preceded
    by a backslash (``\``). To include a backslash itself use ``\\``. The
    backslash escaping is done during parsing of the expression. Any string
    substitution is then performed for each context independently. As such,
    escape backslashes intended to preserve literal meanings of other
    characters during variable substitution must be written as ``\\``.

    Alternatively strings may be enclosed by single quotes (``'``). Such
    strings span from the first single quote until the next. Any character in
    between is taken verbatim and is not subject to any string substitution.

    Examples::

        "foo"
        "${ENABLED}"
        "$(match,${LICENSE},GPL)"

.. _bobpaths_string_function_calls:

String function calls
~~~~~~~~~~~~~~~~~~~~~
    String functions may be called directly without relying on string
    substitution.  The general syntax is the funcion name, an opening
    parenthesis, zero or more arguments separated by comma and a closing
    parenthesis.

    The following two lines are semantically equivalent::

        "$(match,${LICENSE},GPL)"
        match("${LICENSE}", "GPL")

The primitives can be combined with a number of operators. The following table
lists all operators sorted by decreasing precedence. Operator precedence may be
overruled by using parenthesis. The result of all operators is always a
boolean. String comparison is done character by character, based on the Unicode
code point. If the end of string is reached the string lengths are compared.

======== ============= ================== ====================================
Operator Associativity Operand type       Meaning
======== ============= ================== ====================================
``!``    Right         String or boolean  Logical NOT.
``<``    Left          String             Strictly less than.
``<=``   Left          String             Less than or equal.
``>``    Left          String             Strictly greater than.
``>=``   Left          String             Greater than or equal.
``==``   Left          String             Equal.
``!=``   Left          String             Not equal.
``&&``   Left          String or boolean  Logic AND.
``||``   Left          String or boolean  Logic OR.
======== ============= ================== ====================================

See the following examples for some complex expressions:

* ``"${FOO}" == "bar"`` selects packages which use variable ``FOO`` an where
  the value is ``bar``
* ``!match("${LICENSE}", "GPL") && *[ match("${LICENSE}", "GPL") ]`` selects
  packages that are *not* GPL-licensed and depend on a GPL-licensed package

.. _manpage-bobpaths-aliases:

Alias substitution
------------------

Aliases allow a string to be substituted for the first step of a
:ref:`relative location path <manpage-bobpaths-locationpath>`. Absolute
location paths (e.g.  ``/foo``) and relative location paths in predicates (e.g.
``*[ foo ]``) are not not subject to alias substitution. Aliases are only
substituted once. It is therefore not possible to reference an alias from
another alias definition.

Example definitions::

   alias:
      myApp: "host/files/group/app42"
      allTests: "//*-unittest"
      myAppDeps: "myApp/*"

Given the definitions above the following substations will be performed:

======================= ===========================
Query                   Substituted query
======================= ===========================
myApp                   host/files/group/app42
/myApp                  /myApp
myAppDeps               myApp/\*
foo/myApp               foo/myApp
myApp/lib               host/files/group/app42/lib
allTests/\*[myAppDeps]  //\*-unittest/\*[myAppDeps]
======================= ===========================

