#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

# If the fingerprinting is enabled by a tool that is only used in the package
# step then the build step must not be fingerprinted. Make sure that the
# fingerprintScript is not run in the context of the buildScript.

cleanup
run_bob dev root
