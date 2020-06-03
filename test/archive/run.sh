#!/bin/bash -e
. ../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

cleanup
rm -rf default.yaml

# setup local archive
trap 'rm -rf "${archiveDir}"' EXIT
archiveDir=$(mktemp -d)
cat >default.yaml <<EOF
archive:
  -
    backend: file
    path: "$archiveDir"
EOF

# fill archive
run_bob build --download=no --upload root-alpha root-bravo
FINGERPRINT=Alice run_bob build --force --download=no --upload root-alpha root-bravo

pushd $archiveDir
run_bob archive scan

# selectively keep one half
run_bob archive clean --dry-run 'metaEnv.TYPE == "alpha"'
run_bob archive clean -v 'metaEnv.TYPE == "alpha"'
popd

# alpha can be downloaded, bravo must fail
rm -rf work
run_bob build --download=forced root-alpha
expect_fail run_bob build --download=forced root-bravo

# Update archive and rescan. Add some invalid files too.
rm -rf work $archiveDir/*
run_bob build --force --download=no --upload root-alpha root-bravo
pushd $archiveDir
mkdir -p 64/ad
tar zcf 64/ad/6386bae45ebd6788e404758a247e26e5c778-1.tgz /dev/zero
touch 64/ad/aabbcc-too-short.tgz
run_bob archive scan
popd

# Test that -v doesn't catch fire
run_bob archive scan -v
run_bob archive clean -v 'metaEnv.TYPE == "alpha"'

# Copy coverage data from archive directory (if any).
cp $archiveDir/.coverage* . 2>/dev/null || true
