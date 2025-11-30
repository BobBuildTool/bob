#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."

# Tests the different aspects of tool remapping for dependencies.

exec_blackbox_test
