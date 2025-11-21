#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."

expect_fail run_bob ls
