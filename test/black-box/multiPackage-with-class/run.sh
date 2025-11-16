#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."

# Classes and multiPackage instances are separate name spaces. This test case
# makes sure that the anonymous base class in a multiPackage does not clash
# with a class of the same name.

exec_blackbox_test
