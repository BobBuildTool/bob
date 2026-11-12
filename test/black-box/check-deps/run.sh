#!/bin/bash -e
#
# Verify that modifications of dependency workspaces are detected
# (--check-deps) when steps are executed outside of a sandbox.
#
source "$(dirname "$0")/../../test-lib.sh" "../../.."
cleanup

# Well behaving steps pass in release mode where the check is the default
run_bob build root
expect_equal "dep" "$(cat "$(run_bob query-path -f '{dist}' --release root)/dep.txt")"

# Modifying a dependency or the own sources fails by default in release mode
expect_fail run_bob build root -DMODIFY=dep
expect_fail run_bob build root -DMODIFY=src
expect_fail run_bob build root -DMODIFY_PKG=build

# The modified dependency must have been invalidated and is rebuilt
run_bob build root
expect_equal "dep" "$(cat "$(run_bob query-path -f '{dist}' --release root/dep)/result.txt")"
expect_equal "dep" "$(cat "$(run_bob query-path -f '{dist}' --release root)/dep.txt")"

# The check can be disabled
run_bob build --no-check-deps root -DMODIFY=dep

# The check is disabled by default in development mode but can be enabled
run_bob dev root -DMODIFY=dep
expect_fail run_bob dev --check-deps root -DMODIFY=src
expect_fail run_bob dev --check-deps root -DMODIFY=dep
run_bob dev --check-deps root
expect_equal "dep" "$(cat "$(run_bob query-path -f '{dist}' --develop root/dep)/result.txt")"
expect_equal "dep" "$(cat "$(run_bob query-path -f '{dist}' --develop root)/dep.txt")"

# Make sure the check also works in parallel builds
expect_fail run_bob dev --check-deps -j4 root -DMODIFY=dep
