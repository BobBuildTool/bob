#!/bin/bash -e
. ../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

# Test ls
run_bob -C elsewhere ls > log-ls.txt
diff -u output/log-ls.txt log-ls.txt
rm -f log-ls.txt

# Test build
run_bob -C elsewhere build root
diff -u output/result.txt elsewhere/work/root/dist/1/workspace/result.txt
