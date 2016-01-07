Basics
======

The sandbox must contain at least all required basic tools. That is at least
the follwing tools that are utilized by Bob natively:

* bash
* coreutils (mkdir, mktemp, mv, rm, touch, ...)
* curl
* sha1sum

Depending on the actually used features the following tools are also required
in the root sandbox image:

* 7z
* bzip2
* git
* gzip / gunzip
* svn
* tar
* xz / unxz
* zip / unzip

Creating a sandbox image
========================

On Debian it is quite easy to create a root image by using ``debootstrap``::

    sudo debootstrap --include=bzip2,xz-utils,curl,bc jessie ./sandbox-rootfs

You might chroot into the created environment and install additional packages
if needed::

    sudo chroot ./sandbox-rootfs /bin/bash

Then create an archive from the root file system. Usually you can leave out
various unneeded stuff::

    tar -C sandbox-rootfs -Jc --exclude=./usr/share/man --exclude=./usr/share/doc  \
        --exclude=./usr/share/info -v -f sandbox-rootfs.tar.xz \
        ./bin ./etc ./lib ./sbin ./usr

Create additional images
========================

The rootfs for the sandbox should be kept as minimal as possible. In particular
the rootfs should not include a host compiler if this sandbox is intended for
cross comilation. Instead pack the additionally installed files into another
archive and import that as separate recipe in your project::

    cp -a sandbox-rootfs new-packages
    sudo chroot new-packages/ /bin/bash
    apt-get install ...
    ^D
    diff -u <(cd sandbox-rootfs && find . | sort) <(cd new-packages && find . | sort) \
        | sed -n -e '/^+\./s/^+\.\///p' \
        | grep -v '^var/\|^usr/share/man\|^usr/share/doc|^usr/share/info' \
        > new-packages.files
    tar -C new-packages -Jc --files-from new-packages.files -f new-packages.tar.xz

