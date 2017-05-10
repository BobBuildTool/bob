#!/bin/bash -e
. ../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

# Classes that inherit themselves must raise a parse error
pushd len-1
expect_fail run_bob dev root
popd

# Classes that inherit each other must fail too
pushd len-2
expect_fail run_bob dev root
popd
