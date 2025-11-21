#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."
cleanup
rm -rf archive

# Test that indeterministic checkouts are detected when downloading artifacts.
pushd new-behaviour
run_bob dev root -D VARIANT=one --upload

# Building another variant makes sure that no artifact is found. But Bob will
# still predict the checkout step. This prediction will be wrong and should be
# detected.
cleanup
expect_fail run_bob dev root -D VARIANT=two

popd

# The old behaviour was to ignore unexpected indeterministic checkouts and just
# restart the build. Verify that this is still working.
pushd old-behaviour
run_bob dev root -D VARIANT=one --upload
cleanup
run_bob dev root -D VARIANT=two
popd
