#!/bin/bash -e
. ../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

# Test the various direct properties of fingerprint scripts
# =========================================================
#
# The following tests assure that fingerprint scripts have the inteded impact
# onto the package itself.

cleanup
rm -rf output
mkdir -p output/{1,2}

# Run once, change fingerprint, run again -> has to rebuild
FINGERPRINT_ROOT=r1 FINGERPRINT_TOOL=t1 run_bob dev root --destination output/1
FINGERPRINT_ROOT=r2 FINGERPRINT_TOOL=t2 run_bob dev root --destination output/2
expect_fail cmp output/1/result.txt output/2/result.txt

# Unset variable in fingerprint script -> has to fail build
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

# Test variant impact of fingerprint scripts
# ==========================================
#
# A fingerprint script must lead to separate packages for host and sandbox
# builds. Having two distinct sandboxes for the same recipe must lead to two
# independent builds.

# Without sandbox the two packages must be identical 
rm -rf output/*
run_bob dev sandbox-outside --destination output
cmp output/1/id.txt output/2/id.txt

# With sandbox two distinct packages must be built that correspond to the used
# sandbox image
rm -rf output/*
run_bob build sandbox-outside --destination output
expect_fail cmp output/1/id.txt output/2/id.txt

# Test transitive properties fingerprint scripts
# ==============================================
#
# The fingerprint of a package is transitive wrt. content, that is if another
# downstream package uses its result. On the other hand the fingerprint is
# *not* transitive wrt. behaviour. A fingerprinted tool does not taint a
# package that uses this tool.

# Using results is transitive. Upload once. Download with other fingerprint
# must fail. Using same fingerprint again must download.
rm -rf dev work
FINGERPRINT_ROOT=x1 run_bob build transitive-downstream --upload
FINGERPRINT_ROOT=x2 expect_fail run_bob dev transitive-downstream --download forced
FINGERPRINT_ROOT=x1 run_bob dev transitive-downstream --download forced

# Uploading and downloading with different fingerprint is supposed to work.
rm -rf dev work
FINGERPRINT_TOOL=y1 run_bob build transitive-consumer --upload
FINGERPRINT_TOOL=y2 run_bob dev transitive-consumer downstream--download forced

# Test conditional fingerprinting
# ===============================

CANARY="$PWD/output/canary"

run_bob dev -D CANARY="$CANARY" conditional-never --destination output
expect_fail test -e "$CANARY"
if [[ $(< output/result.txt) != $CANARY ]] ; then
	echo wrong result >&2
	exit 1
fi

run_bob dev conditional-default --destination output
expect_fail test -e "$CANARY"
if [[ $(< output/result.txt) != unset ]] ; then
	echo wrong result >&2
	exit 1
fi

run_bob dev -D CANARY="$CANARY" conditional-default --destination output
test -e "$CANARY"
rm "$CANARY"
if [[ $(< output/result.txt) != $CANARY ]] ; then
	echo wrong result >&2
	exit 1
fi

run_bob dev conditional-always --destination output
expect_fail test -e "$CANARY"
if [[ $(< output/result.txt) != dummy ]] ; then
	echo wrong result >&2
	exit 1
fi
