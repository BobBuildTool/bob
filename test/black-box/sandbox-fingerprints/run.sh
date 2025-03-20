#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

# Build a fingerprinted artifact inside and outside of a sandbox. The
# fingerprint script yields the same result. Thus the artifact should be shared
# between the builds. Also verify that the sandbox does not need to be
# built/downloaded to calculate the fingerprint in the sandbox build.

cleanup

# Check if namespace feature works on this host.
"${BOB_ROOT}/bin/bob-namespace-sandbox" -C || skip

# Create a temporary archive location. Make sure it is cleaned up at exit.
ARCHIVE="$(mktemp -d)"
trap 'rm -rf $ARCHIVE' EXIT
cat >default.yaml <<EOF
archive:
    name: "local"
    backend: file
    path: "$ARCHIVE"
whitelist:
    - FAIL_FINGERPRINT_IF_SET
EOF

# Test the test: must fail in the fingerprintScript
FAIL_FINGERPRINT_IF_SET=1 expect_fail run_bob dev --sandbox --upload root

# First build in the sandbox, upload and then verify that we can download it
# directly in the non-sandbox build.
run_bob dev --sandbox --upload root
rm -rf dev/
run_bob dev --download forced root

# Verify that the sandbox build does not need the sandbox image but that the
# fingerprint was cached.
cleanup
FAIL_FINGERPRINT_IF_SET=1 run_bob dev --sandbox --download forced root
if [[ $(ls dev/dist/ | wc -l) -ne 1 ]] ; then
	echo "Expected only one built package" >&2
	exit 1
fi

# Verify that the fingerprint of the sandbox was cached locally.
rm -rf dev
FAIL_FINGERPRINT_IF_SET=1 run_bob dev --sandbox --download=no root
