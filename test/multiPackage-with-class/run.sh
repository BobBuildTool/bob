#!/bin/bash -e
. ../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

# Classes and multiPackage instances are separate name spaces. This test case
# makes sure that the anonymous base class in a multiPackage does not clash
# with a class of the same name.

exec_blackbox_test
