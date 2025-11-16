#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."

exec_blackbox_test

# check for correct amount of packages
run_bob ls -rp > log-cmd.txt
diff -uZ log-cmd.txt packages.txt
