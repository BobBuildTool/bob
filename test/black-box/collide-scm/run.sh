#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."

# Test that new checkouts check for clean work space

cleanup

mkdir -p dev/src/root/1/workspace
touch dev/src/root/1/workspace/b.tgz

expect_fail run_bob dev root
