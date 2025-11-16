#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."

cleanup

exp="$(mktemp -d)"
trap 'rm -rf "$exp"' EXIT

run_bob jenkins add local http://example.test/ -r root -o jobs.update=lazy

# cannot add twice
expect_fail run_bob jenkins add local http://another.test/

run_bob jenkins graph local

# Plugins smoke test
run_bob jenkins export local "$exp"
grep -q "test.bob.canary" "$exp/root.xml"

run_bob jenkins ls
run_bob jenkins ls -v
run_bob jenkins ls -vv

run_bob jenkins rm local

# cannot delete twice
expect_fail run_bob jenkins rm local
