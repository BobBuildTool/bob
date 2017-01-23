#!/bin/bash -e
. ../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }
cleanup

# If a step is run Bob must not rely on the old state of the workspace anymore.
# Failing steps must thus always be run again.

# build normally
run_bob dev root

# introduce some error in the sources -> expected fail
echo "#error fail" > dev/src/root/1/workspace/foo.c
if run_bob dev root 2>/dev/null ; then
	echo "Expected fail"
	exit 1
fi

# run again and force package step
rm dev/src/root/1/workspace/foo.c
run_bob dev root -DFOO=bar
