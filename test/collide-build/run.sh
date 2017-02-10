#!/bin/bash -e
. ../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

# Test that an unexpected build work space with old files is deleted

cleanup
mkdir -p dev/dist/root/1/workspace
echo "garbage" > dev/dist/root/1/workspace/garbage.txt
run_bob dev root
RES="$(run_bob query-path -f '{dist}' root)"
diff -Nurp $RES output
