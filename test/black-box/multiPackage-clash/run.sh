#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."

# bob must fail because of package name clash
expect_fail run_bob ls
