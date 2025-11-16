#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."
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
