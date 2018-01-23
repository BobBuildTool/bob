# Bob build tool
# Copyright (C) 2018 Ralf Hubert
#
# SPDX-License-Identifier: GPL-3.0

""" Fossology License scanner

This scanner is designed to work with the fossology docker coming along
with bob. (see contrib/fossy_docker).

It uses scp for file upload and ssh to execute fossology commandline
tools, as there is no stable web API for doing this.

"""

from bob.errors import BuildError
from bob.tty import colorize
import os
import re
import stat
import subprocess

class FossologyLicenseScanner:
    def getLicenseFile(name, prettySrcPath):
        # make a temporary directory to upload sources
        try:
            cmd = "ssh -p 8082 bob@localhost mktemp -d"
            tmpDir = subprocess.check_output(cmd.split(" "), universal_newlines=True).strip()
        except subprocess.CalledProcessError as e:
            raise BuildError("Fossology: error while uploading: " + str(e))

        try:
            # upload sources using scp
            cmd = "scp -P 8082 -p -r " + prettySrcPath + os.sep + ". bob@localhost:" + tmpDir
            subprocess.call(cmd.split(" "), stdout=subprocess.DEVNULL)
            cmd = "ssh -p 8082 bob@localhost chmod -R +r " + tmpDir + os.sep + "."
            subprocess.call(cmd.split(" "))
            # call cp2foss with alpha-foldes and scedule a nomos scan
            # parse the output for the uploadId as we need this ID for downloading
            # the spdx afterwards
            cmd = "ssh -p 8082 bob@localhost cp2foss {} \
                    -A -d {} --username fossy --password fossy -n {} \
                    -q agent_nomos".format(tmpDir, name, name)
            output = subprocess.check_output(cmd.split(" "), universal_newlines=True).strip()
            # we need the uploadPKG for downloading SPDX
            sId = re.search("UploadPk is:.*'([0-9]*)'", output, re.IGNORECASE)
            if (sId):
                uploadPk = sId.group(1)
            else:
                raise BuildError("Unable to find uploadPk in cp2foss output.")
        except subprocess.CalledProcessError as e:
            raise BuildError("Fossology: error while uploading: " + str(e))

        # remove the temporary directory
        try:
            cmd = "rm -rf " + tmpDir
            subprocess.call(cmd.split())
        except subprocess.CalledProcessError as e:
            raise BuildError("Fossology: error while uploading: " + str(e))

        # wait for SPDX ready + download the file
        script = """#!/bin/bash
repo=localhost:8081/repo/
upload={UPLOAD}
cookieJar="$(mktemp)"

curl --silent "$repo"'?mod=auth' --data-urlencode 'username=fossy' --data-urlencode 'password=fossy' -e "?mod=" -c "$cookieJar" >& /dev/null

reportUrl="$(
   curl --silent "$repo"'?mod=ui_spdx2&upload='$upload -b "$cookieJar" 2>&1 | sed -ne 's/.*\(?mod=download&report=[0-9]*\).*/\\1/p' | head -n 1
)"

if [[ ! -n "$reportUrl" ]]; then
    echo "something went wrong"
    exit 1
fi

STATUS=1
while [ $STATUS ]; do
    sleep 1
    STATUS=$(curl --silent -f "$repo/$reportUrl" -b $cookieJar -o {OUTPUT})
    grep "Missing report" {OUTPUT}
    if [ $? == 0 ]; then
        STATUS=1
    fi
done
rm $cookieJar"""
        scriptFile = os.path.join(prettySrcPath, '..', "spdx_download.sh")
        with open(scriptFile, 'w') as f:
            f.write(script.format(UPLOAD=uploadPk,
                OUTPUT=os.path.join(prettySrcPath, '..', 'license.spdx')))

        os.chmod(scriptFile, stat.S_IRWXU | stat.S_IRGRP | stat.S_IWGRP |
            stat.S_IROTH | stat.S_IWOTH)
        try:
            subprocess.call(['/bin/bash', '-c', scriptFile], stdout=subprocess.DEVNULL)
        except subprocess.CalledProcessError as e:
            raise BuildError("Fossology: error while downloading SPDX: " + str(e))
