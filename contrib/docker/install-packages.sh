#!/bin/bash

set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get -y upgrade
apt-get -y install python3 python3-pip python3-sphinx
apt-get -y install tar git subversion xz-utils gzip unzip bzip2 p7zip-full gcc g++ make gawk m4 perl rsync plzip
python3 -m pip install BobBuildTool --break-system-packages

# cleanup
apt-get clean
rm -rf /var/lib/apt/lists/*

# add non-root user
useradd -ms /bin/bash bob
