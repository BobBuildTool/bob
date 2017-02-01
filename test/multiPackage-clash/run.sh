#!/bin/bash -e
. ../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

# bob must fail because of package name clash
expect_fail run_bob ls
