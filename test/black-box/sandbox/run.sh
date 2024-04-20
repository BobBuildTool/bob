#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

# Run a simple sandbox that mounts the full host. Just see if exeuction works.

cleanup

# Check if namespace feature works on this host.
"${BOB_ROOT}/bin/bob-namespace-sandbox" -C || skip

run_bob build -DFOO=bar root
run_bob dev --sandbox -E root

# Check that we can keep our UID or even be root inside sandbox
run_bob dev --sandbox -c as-self -DEXPECT_UID=$UID root
run_bob dev --sandbox -c as-root -DEXPECT_UID=0 root
