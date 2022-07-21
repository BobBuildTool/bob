#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }
cleanup

# run with default settings
run_bob dev root-alpha root-bravo
expect_exist dev/src/lib1-special/1/workspace
expect_exist dev/src/lib2/1/workspace
expect_exist dev/{build,dist}/lib{1,2}/{alpha,bravo}/1/workspace
expect_exist dev/{build,dist}/root-alpha/alpha/1/workspace
expect_exist dev/{build,dist}/root-bravo/bravo/1/workspace

run_bob build root-alpha root-bravo
expect_exist work/lib1-special/src/1/workspace
expect_exist work/lib2/src/1/workspace
expect_exist work/lib{1,2}/{alpha,bravo}/{build,dist}/1/workspace
expect_exist work/root-alpha/alpha/{build,dist}/1/workspace
expect_exist work/root-bravo/bravo/{build,dist}/1/workspace
