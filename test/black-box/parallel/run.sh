#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }
cleanup

run_bob dev root -j4 -k

expect_fail run_bob dev root -j4 -k -DFAIL_LIB1=1
expect_fail run_bob dev root -j4 -k -DFAIL_LIB1=1 -DFAIL_LIB2=1
