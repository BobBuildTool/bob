#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."

# Classes that inherit themselves must raise a parse error
pushd len-1
expect_fail run_bob dev root
popd

# Classes that inherit each other must fail too
pushd len-2
expect_fail run_bob dev root
popd
