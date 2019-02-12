#!/bin/bash -e
. ../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

# Test the various properties of fingerprint scripts.

cleanup
rm -rf output
mkdir -p output/{1,2}

# Run once, change fingerprint, run again -> has to rebuild
FINGERPRINT_ROOT=r1 FINGERPRINT_TOOL=t1 run_bob dev root --destination output/1
FINGERPRINT_ROOT=r2 FINGERPRINT_TOOL=t2 run_bob dev root --destination output/2
expect_fail cmp output/1/result.txt output/2/result.txt

# Unset variable in fingperint script -> has to fail build
expect_fail run_bob dev root

# Upload with one fingerprint, try to download with another -> has to fail
rm -rf output
mkdir -p output/{1,2}
FINGERPRINT_ROOT=r1 FINGERPRINT_TOOL=t3 run_bob dev root --upload --destination output/1
FINGERPRINT_ROOT=r1 FINGERPRINT_TOOL=t4 expect_fail run_bob dev root --download forced

# Upload with other fingerprint -> check number of artifacts
FINGERPRINT_ROOT=r2 FINGERPRINT_TOOL=t3 run_bob dev root --upload
if [[ $(find output -name '*.tgz' | wc -l) -ne 2 ]] ; then
	echo "Expected two artifacts in repo!" >&2
	exit 1
fi

# Download the old artifact from the first upload
FINGERPRINT_ROOT=r1 FINGERPRINT_TOOL=t3 run_bob dev root --download forced --destination output/2
cmp output/1/result.txt output/2/result.txt

# Building the same again must not change anything
FINGERPRINT_ROOT=r1 FINGERPRINT_TOOL=t3 run_bob dev root --download forced --destination output/2
cmp output/1/result.txt output/2/result.txt
