#!/bin/bash -e
. ../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

# Test that new checkouts check for clean work space

cleanup

mkdir -p dev/src/root/1/workspace
touch dev/src/root/1/workspace/b.tgz

expect_fail run_bob dev root
