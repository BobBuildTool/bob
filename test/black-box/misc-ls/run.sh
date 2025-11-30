#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."

# Test various invocations of "bob ls"

cleanup

run_bob ls
run_bob ls -r
run_bob ls -d '/*'
run_bob ls -rp > log-cmd.txt
diff -uZ log-cmd.txt output/ls-rp.txt
run_bob ls -rpa > log-cmd.txt
diff -uZ log-cmd.txt output/ls-rpa.txt

expect_output "" run_bob ls foo
expect_output "" run_bob ls root/d
