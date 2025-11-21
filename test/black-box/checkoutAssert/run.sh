#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."
cleanup

# checkoutAssert shouldn't trigger in this test
run_bob dev root -DGPL3_START=1 -DGPL3_1_4_SHA1=158b94393dfdad277ff26017663c1c56676aaa84

# checkoutAssert should make the build fail in this test
expect_fail run_bob dev root_fail
