#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."
cleanup

# Test that --checkout-only does what it says. Additionally it checks that
# tools that are used during a checkout step are still built.

# checkout sources
run_bob dev root --checkout-only

# compare result
diff -NurpZ output/app "$(run_bob query-path -f '{src}' root/app)"
diff -NurpZ output/lib "$(run_bob query-path -f '{src}' root/app/lib)"
