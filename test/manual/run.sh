#!/bin/bash -e
. ../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

# Manually execute step in the different variants.

cleanup
run_bob dev root

run_bob _invoke dev/dist/root/1/step.spec
run_bob _invoke dev/dist/root/1/step.spec -c
run_bob _invoke dev/dist/root/1/step.spec -i
run_bob _invoke dev/dist/root/1/step.spec -E
run_bob _invoke dev/dist/root/1/step.spec -n

run_bob _invoke dev/dist/root/1/step.spec -qq
run_bob _invoke dev/dist/root/1/step.spec -q
run_bob _invoke dev/dist/root/1/step.spec -v
run_bob _invoke dev/dist/root/1/step.spec -vv

run_bob _invoke dev/dist/root/1/step.spec shell </dev/null
run_bob _invoke dev/dist/root/1/step.spec shell -E </dev/null

run_bob _invoke dev/dist/root/1/step.spec fingerprint
