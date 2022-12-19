#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }
cleanup

run_bob dev root -j4 -k
expect_fail run_bob dev root -j4 -k -DFAIL_LIB1=1
expect_fail run_bob dev root -j4 -k -DFAIL_LIB1=1 -DFAIL_LIB2=1

if is_posix ; then
	# Run on pty to excercise the interactive GUIs
	for j in 1 4 32; do
		run_bob_tty dev root -j $j -k
		expect_fail run_bob_tty dev root -j $j -k -DFAIL_LIB1=1
		expect_fail run_bob_tty dev root -j $j -k -DFAIL_LIB1=1 -DFAIL_LIB2=1
	done
fi
