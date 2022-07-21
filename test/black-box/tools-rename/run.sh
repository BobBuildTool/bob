#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

# Tests the different aspects of tool remapping for dependencies.

exec_blackbox_test
