#!/bin/bash -e
. ../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

# Test various invocations of "bob ls"

cleanup

run_bob ls
run_bob ls -r
run_bob ls -rp > log-cmd.txt
diff -u log-cmd.txt output/ls-rp.txt
run_bob ls -rpa > log-cmd.txt
diff -u log-cmd.txt output/ls-rpa.txt

expect_fail run_bob ls foo
expect_fail run_bob ls root/d
