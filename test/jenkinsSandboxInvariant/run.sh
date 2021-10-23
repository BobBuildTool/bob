#!/bin/bash -e
. ../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

# Regression test for #438 to see if jenkins jobs can still be generated even
# if identical package (prog) is built inside and outside of sandbox in same
# project.

cleanup

exp="$(mktemp -d)"
trap 'rm -rf "$exp"' EXIT

run_bob jenkins add local http://example.test/ -r root
run_bob jenkins export local "$exp"
