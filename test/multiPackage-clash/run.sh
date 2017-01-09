#!/bin/bash -e
. ../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

# bob must fail because of package name clash
if run_bob ls > log.txt 2>&1 ; then
	echo "Parsing must fail!"
	exit 1
fi
