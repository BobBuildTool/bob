#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }
cleanup
rm -rf archive

# Test that indeterministic checkouts are detected when downloading artifacts.

run_bob dev root -D VARIANT=one --upload

cleanup

# Building another variant makes sure that no artifact is found. But Bob will
# still predict the checkout step. This prediction will be wrong and should be
# detected.

expect_fail run_bob dev root -D VARIANT=two
