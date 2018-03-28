#!/bin/bash -e
. ../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }
cleanup

# checkoutAssert shouldn't trigger in this test
run_bob dev root

# checkoutAssert should make the build fail in this test
expect_fail run_bob dev root_fail
