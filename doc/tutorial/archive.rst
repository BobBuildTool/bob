Binary archive management
*************************

Bob supports binary archives with only minimal configuration. The up- and
downloaded artifacts do not need to be versioned manually. Instead, the
recipes, their configuration and the actual source code is used as an index
into a content-addressed artifact cache. See
:ref:`concepts-implicit-versioning` and the :term:`Build-Id` in particular for
the mechanisms that are used to store and find binary artifacts.

Basic configuration
===================

To use some binary artifact cache, add one or more backends in the
:ref:`configuration-config-archive` section of the user configuration.
Regardless of the chosen backends, the cache directory layout is always
compatible. That is, for example a locally executed build can use the ``file``
backend to populate the binary artifact cache which in turn can be served by an
HTTP server and remote clients can use the artifacts with the ``http`` backend.

Example::

   archive:
      backend: http
      url: "http://localhost:8001/upload"

Local caching
=============

The download of artifacts is considered "cheap" by Bob. Imagine ``bob dev
package`` downloaded all dependencies. Suppose the user changes one of those
dependencies, e.g. ``package/foo``.  Now Bob has to build this package and
discards the downloaded files. If the user reverts the changes, the original
version of ``package/foo`` can and will be downloaded again.

If the connection to the artifact server is slow, this might pose a significant
overhead. It is possible to declare one or more backends as a "caching".
Successfully downloaded artifacts will be automatically uploaded to such
backends. This is best done in the user configuration
(``~/.config/bob/default.yaml``) which affects all projects::

   archivePrepend:
      - backend: file
        path: "~/.cache/bob/artifacts"
        flags: [download, cache]

Artifact upload
===============

Artifacts must be uploaded explicitly. If enabled, the upload happens after a
package has been built. Use the ``--upload`` switch, regardless of local- of
Jenkins-builds.

Example HTTP server configurations
==================================

Most web servers provide some basic WebDAV support. In any case, it is probably
a good idea to apply some authentication for uploads. Otherwise, anybody with
network access to the server could manipulate artifacts.

Nginx
-----

For a read-only access to a binary artifact repository, no special
configuration is necessary. If you want to allow uploads too, the location must
have at least the basic WebDAV methods enabled::

     location / {
         root /srv/bob;

         client_max_body_size 0;
         client_body_temp_path  /srv/tmp;

         dav_methods PUT MKCOL;

         create_full_put_path on;
     }

The ``create_full_put_path`` directive is only necessary for Bob versions prior
to 0.25. Most importantly, the ``client_max_body_size`` lifts the maximum
upload size limit. The default is just 1 MiB which would be very much too low
for any package. Equally important, the directory of the
``client_body_temp_path`` directive should be located on the same file system
as the location itself. All uploads are first stored in the temporary path and
will be moved to the final location as the last step.

.. attention::
   If you are using Nginx prior to version 1.21.4 you need to add a
   ``sendfile_max_chunk 2m;`` directive. Otherwise the download of files
   larger than 2 GiB will fail on Linux.

Even though not yet available, Bob will probably gain archive maintenance
support through the :ref:`manpage-archive` command in the future. This will
require full WebDAV support. The Nginx core server does not support all
required methods, though.  Fortunately, the external nginx-dav-ext-module
provides the missing methods. If enabled, add the following to the location::

    location / {
         dav_methods PUT DELETE MKCOL COPY MOVE;
         dav_ext_methods PROPFIND OPTIONS;
    }

Garbage collection
==================

Especially on CI builds, the number of stored artifacts can grow significantly.
The uploaded artifacts can be managed by :ref:`manpage-archive`. It might also
be a good idea to use different repositories for release builds and for
continuous builds to keep them separated.
