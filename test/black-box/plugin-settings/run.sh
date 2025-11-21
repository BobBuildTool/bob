#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."
cleanup

# run with default settings
run_bob dev root
RES=$(run_bob query-path -f '{dist}' --develop root)
diff -uZ $RES/result.txt output/default.txt

# override settings
run_bob dev root -c cfg1
diff -uZ $RES/result.txt output/one.txt

run_bob dev root -c cfg2
diff -uZ $RES/result.txt output/two.txt
