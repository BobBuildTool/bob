#!/bin/bash -e
. ../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

exec_blackbox_test

# check for correct amount of packages
run_bob ls -rp > log-cmd.txt
diff -u log-cmd.txt packages.txt
