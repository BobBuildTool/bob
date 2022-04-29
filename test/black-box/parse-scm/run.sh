#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

run_bob ls > log-ls.txt
diff -Nu output/log-ls.txt log-ls.txt
rm -f log-ls.txt
