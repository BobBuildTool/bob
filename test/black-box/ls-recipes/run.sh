#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }
cleanup

# does not work on native windows due to line endings
if is_win32 ; then
        skip
fi

expect_output "lib
root
unused" run_bob ls-recipes

expect_output "lib
root" run_bob ls-recipes --used

expect_output "unused" run_bob ls-recipes --orphaned

expect_output "unused	recipes/unused.yaml" run_bob ls-recipes --orphaned --sources
