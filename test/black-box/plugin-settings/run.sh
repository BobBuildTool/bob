#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }
cleanup

# run with default settings
run_bob dev root
RES=$(run_bob query-path -f '{dist}' --develop root)
diff -u $RES/result.txt output/default.txt

# override settings
run_bob dev root -c cfg1
diff -u $RES/result.txt output/one.txt

run_bob dev root -c cfg2
diff -u $RES/result.txt output/two.txt
