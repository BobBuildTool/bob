Concepts
========

Bob is, at its core, just another package build system. Conceptually it is divided
in two parts: a front end that reads a domain specific language that describes
how to build packages and several backends that can build these packages.

Recipes, classes and Packages
-----------------------------

All build information for Bob is declared in so called *recipes*. Effectively
they are blueprints for what should be built. The mathematical therm for Bob's
recipes would be that of a "function". A *package* is the result of the
function, that is when the recipe was "computed". To keep things simple common
parts of multiple recipes might be stored in *classes*.

Some parts of the recipe may depend on additional information that is provided
by other recipes. The computed result of a recipe, where all inputs are
resolved, is called a package.  Each package is created from a single recipe
but there might be multiple packages that are created from a particular recipe.
Inside each recipe there are always three steps: checkout, build and package.
The following picture shows the processing inside a recipe and the interaction
with upstream and downstream recipes:

.. image:: /images/recipe-flow.png

There are four kinds of objects that are exchanged between recipes: results,
dependencies, environment variables and tools. Results (shown as black arrows)
are always propagated upstream. These are the actual build artifacts that are
created. Dependencies (downstream recipes) may be propagated upwards which is
not shown in the picture. Environment variables are key-value-pairs of
strings. They are passed as shell variables to the individual build steps.
Tools are scripts or executables that are needed to produce the build result,
e.g. compilers, image generator or post processing scripts. Consuming recipes
of tools get the directory of the required tool added to their $PATH so that
they are available for the build steps. By explicitly defining the dependencies
and required environment variables/tools of a recipe Bob can track changes that
influence the build result with a high degree of certainty.

The actual processing during build time is done in the orange steps. They are
Bash scripts that are executed with (and only with) the declared environment
and tools. Bob assumes that the result of the steps depends only on the scripts
themselves, their environment and tools. Additionally Bob assumes that the
"build" and "package" steps are deterministic in the sense that they produce
the equivalent result for the same input. Equivalent results may not
necessarily be bit identical but must have the same function and must thus be
interchangeable. This property is required to reuse binary build results from
previous build runs or from external build servers.

The flow of environment variables is depicted in red while the flow of tools is
shown in green. By default only build results and dependencies are exchanged. A
recipe may declare that it consumes certain environment variables
({checkout,build,package}Consume) and tools ({checkout,build,pac kage}Tools).
On the other hand a recipe may also declare to provide a dependency, tool or
variable, providing the necessary input for upstream recipes. If a tool or
environment variable is used without declaring its usage Bob will stop
processing. This will either happen when parsing the recipes (if detectable) or
during execution of the build. All executed scripts are configured to fail if
an undefined variable is used or any command returns a failure status.

Implicit versioning
-------------------

A key concept of Bob is that recipes do not have an explicit version. Instead,
Bob constructs an implicit version that is derived from the recipe and the
input to this recipe when building a particular package. This is called the
Package-Id. The recipe language is static in the sense that the Package-Id of a
package can be calculated in advance without executing any build steps. This
enables Bob to determine exactly when a package has to be built from scratch
(e.g. the build script changed) or if Bob has to build several packages from
the same recipe due to varying input parameters.

Technically the Package-Id is the Variant-Id of the package step. The Variant-Id of
each step (checkout, build and package) is calculated as follows:

.. math::

   Id_{variant}(step) = H_{md5}(script_{utf8} || \lbrace Id_{variant}(t) || RelPath_{utf8}(t) || LibPaths_{utf8}(t) : t \in tools \rbrace || env|| \lbrace Id_{variant}(i) : i \in input \rbrace )

where

* *script* is the Bash script of the step,
* *tools* is the sorted list of tools that are consumed by the step,
* *env* is the sorted list of the environment key-value-pairs and
* *input* is the list of all results that are passed to the step (i.e. previous step, dependencies).

There exists also a second implicit version, called Build-Id, which identifies
the build result in advance. The Build-Id can be used to grab matching build
artifacts from another build server instead of building them locally. The
Build-Id is derived from the source specification of the checkout step
(commit-Ids, SVN URL+revision, ...), the build/package Scripts, the environment
and all Build-Ids of the recipe dependencies:

.. math::

    Id_{build}(step) =
    \begin{cases}
        H_{md5}(script_{utf8} || \lbrace Id_{build}(t) || RelPath_{utf8}(t) || LibPaths_{utf8}(t) : t \in tools \rbrace || env || \lbrace Id_{build}(i) : i \in input \rbrace ) \\
        H_{sha1}(src)
    \end{cases}

where

* *script* is the symbolic script of the step,
* *tools* is the sorted list of tools that are consumed by the step,
* *env* is the sorted list of the environment key-value-pairs and
* *input* is the list of all results that are passed to the step (i.e. previous step, dependencies).

The special property of the Build-Id is that it represents the expected result.
Actually there are two Build-Ids: a static and a dynamic one. The static
Build-Id of a step is only defined if the step and any of its dependencies is
deterministic. For indeterministic checkouts the dynamic Build-Id is defined
as the hash of the result of the checkout step. Compared to the Package- and
static Build-Id the dynamic Build-Id is not computable in advance but requires
to execute certain checkout steps. To keep the Build-Id stable in the long run
the scripts of SCMs in the checkout step are replaced by a symbolic
representation.

Variant management
------------------

Variant management is handled entirely by environment variables that are passed
to the recipes. Through implicit versioning Bob can determine if multiple
packages have to be built from the same recipe due to varying environment
variables.

.. image:: /images/variant-management.png

Typically variant management will be done by defining a dedicated environment
variable for each feature, e.g. FEATURE_FOO which is either disabled ("0") or
enabled ("1"). A recipe declares that it depends on this variable in the build
step by listing FEATURE_FOO in the buildVars clause. Through this
declaration Bob can selectively set (only) the needed environment variables in
each step and can track their dependency on them.  When building the whole
software Bob can calculate how many variants of the recipe have to be built by
resolving all dependent variables.

Re-usage of build artifacts
---------------------------

When building packages Bob will use a separate directory for each Step-Id.
Future executions of a particular step will use the same directory unless the
step is changed and gets a new Step-Id. By using the Step-Id as discriminator a
safe incremental build is possible. As long as the Step-Id is stable the
previous directory will be reused. If anything is changed that might influence
the build result (step itself or any dependency) it will result in a new
Step-Id and Bob will use a new directory. Likewise, if the changes are
reverted, the Step-Id will get the previous value and Bob will restart using
the previous directory.

In local builds the build results are shared directly with upstream packages by
passing the path to the upstream steps. On the Jenkins build server the build
results are copied between the different work spaces.

Based on the Build-Id it is possible to fetch build results of a build server
from an artifact repository instead of building it locally. To compute the
Build-Id Bob requires that the checkout step of the recipe and all its
dependencies must be deterministic. Then Bob will look up the package result
from the artifact repository based on the Build-Id. If the artifact is found it
will be downloaded and the build and package steps are skipped. Otherwise the
package is built as always. Additionally Bob requires the following properties
from a recipe:

* The build and package steps have to be deterministic. Given the same script
  with the same input it has to produce the same result, functionality wise. It
  is not required to be bit-identical, though.
* The build result must be relocatable. As the build server will very likely
  have used another directory as the local build the result must still work on
  the new place.
* The build result must not contain references to the build machine or any
  dependency. Sometimes the build result contains symlinks that might not be
  valid on other machines.

Under the above assumptions Bob is able to reliably reuse build results from
other build servers.

