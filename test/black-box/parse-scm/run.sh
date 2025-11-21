#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."

run_bob ls > log-ls.txt
diff -NuZ output/log-ls.txt log-ls.txt
rm -f log-ls.txt
