#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }
cleanup

# Test that --checkout-only does what it says. Additionally it checks that
# tools that are used during a checkout step are still built.

# checkout sources
run_bob dev root --checkout-only

# compare result
diff -Nurp output/app "$(run_bob query-path -f '{src}' root/app)"
diff -Nurp output/lib "$(run_bob query-path -f '{src}' root/app/lib)"
