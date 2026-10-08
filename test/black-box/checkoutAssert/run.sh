#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."
cleanup

# checkoutAssert shouldn't trigger in this test
run_bob dev root -DGPL3_START=1 -DGPL3_1_4_SHA1=158b94393dfdad277ff26017663c1c56676aaa84

# checkoutAssert will fail because the start is not a number
expect_fail run_bob dev root -DGPL3_START=z -DGPL3_1_4_SHA1=158b94393dfdad277ff26017663c1c56676aaa84

# checkoutAssert should fail because the digest does not match
expect_fail run_bob dev root -DGPL3_START=1 -DGPL3_1_4_SHA1=0000000000000000000000000000000000000000
