#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

# Run a simple sandbox that mounts the full host. Just see if exeuction works
# even if $PATH is empty in the sandbox.

cleanup

# Check if namespace feature works on this host.
"${BOB_ROOT}/bin/bob-namespace-sandbox" -C || skip

run_bob build -DFOO=bar root
run_bob dev --sandbox -E root
