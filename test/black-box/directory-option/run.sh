#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."

# clean workspace
pushd elsewhere
cleanup
popd

# Test ls
run_bob -C elsewhere ls > log-ls.txt
diff -uZ output/log-ls.txt log-ls.txt
rm -f log-ls.txt

# Test build
run_bob -C elsewhere build root
diff -uZ output/result.txt elsewhere/work/root/dist/1/workspace/result.txt
