.. highlight:: yaml

How to properly tell Bob about host dependencies
************************************************

Bob closely tracks the input of all packages. This includes all checked out
sources and the dependencies to other packages. If something is changed Bob can
accurately determine which packages have to be rebuilt. This information is
also used to find matching binary artifacts. If a recipe depends on resources
that are outside of the declared recipes the situation changes, though. Bob
cannot infer what external resources are actually used and how these influence
the build result.

To make these external resources visible to Bob a ``fingerprintScript`` must be
used. The script is executed and the output is taken as fingerprint for the
external resource. This way Bob can detect if the external resource has been
changed and if a binary artifact is suitable on other machines.  See
:ref:`configuration-principle-fingerprinting` for more details.

Generally speaking fingerprint scripts should only be evaluated in case of a
host-build. For cross-compiling the resources are usually provided by other
recipes. The exception would be a recipe that uses some host resources during
cross compilation, e.g. an IDL compiler that ships a target library too.

Fingerprinting the C/C++ compiler
=================================

The most common fingerprinting application is the host compiler. Usually a
project should define a stub host compiler recipe that just represents the
host compiler. The following example is stripped down for clarity::

   provideTools:
      toolchain:
         path: .
         environment:
            CC: cc
            CXX: c++
         fingerprintIf: true
         fingerprintScript: |
            bob-libc-version
            bob-libstdc++-version
      host-toolchain:
         path: .

By convention the tool name for the C/C++-Toolchain is just called
``toolchain``. The ``fingerprintIf`` of it will unconditionally enable
fingerprinting of whatever package is using this tool. The
``fingerprintScript`` will be added to these packages. In these scripts the
libc and libstdc++ versions are checked via the built-in helpers.

Note that there is a separate ``host-toolchain`` tool that is basically the
same as ``toolchain`` but without the ``fingerprintScript`` and
``fingerprintIf``. This special tool is used in recipes that always need the
host compiler even if they are cross-compiled with a different toolchain. The
Linux kernel is a notable example. The ``fingerprintScript`` is not needed
there because the result of such packages do not depend on the actual host
compiler.

Using external libraries
========================

Using a library from the host adds another dependency that must be declared to
Bob. The example below assumes that the compiler is already fingerprinted as
described in the previous chapter. Generally speaking the fingerprint script
should properly detect the version of the library on the host.

Using pkg-config libraries
--------------------------

If your recipe uses an external library that ships a proper ``.pc`` file it
is usually as simple as calling ``pkg-config`` in the ``fingerprintScript``::

   fingerprintScript: |
      pkg-config --modversion <external-dependency>

Note the absence of a ``fingerprintIf`` in the example. This is left out
deliberately because the recipe should typically not know if it is compiled on
the host or cross-compiled. The toolchain is supposed to enable fingerprinting
if the recipe is compiled for the host. For cross-builds the used
toolchain/SDK is normally provided by another recipe and thus fully known to
Bob. In contrast to that the host toolchain is just a stub recipe but it will
enable the fingerprinting if you follow the suggestion of the first section.

Using a library without meta information
----------------------------------------

If the library you are using does not provide any form of meta information it
must be assumed that it is already in the search path of the linker. Bob
provides a small helper that links a dummy executable to find the actual
library that the linker was using::

   fingerprintScript: |
      bob-hash-libraries ffi

This will let the linker search the library. The result of the fingerprint is
the hash sum of all used libraries, including transitive dependencies. While
this may be more pessimistic than using a version number it is on the other
hand guaranteed to detect different host configurations regarding this library.
