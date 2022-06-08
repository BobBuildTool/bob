#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

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

# first try to keep everything by using multiple expressions
oldNum=$(find -name '*.tgz' | wc -l)
run_bob archive clean -v 'metaEnv.TYPE == "alpha"' 'meta.package == "root-bravo"'
newNum=$(find -name '*.tgz' | wc -l)
test $oldNum -eq $newNum

# Find one of the artifacts
found=$(run_bob archive find 'meta.package == "root-bravo"')
expect_exist "$found"

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

# Test for --fail option
expect_fail run_bob archive scan --fail -v
expect_fail run_bob archive clean --fail -v 'metaEnv.TYPE == "alpha"'
pushd $archiveDir
run_bob archive scan --fail -v
run_bob archive clean --fail -v 'metaEnv.TYPE == "alpha"'
popd

# Test the "LIMIT" feature. Build a number or artifacts and keep only a portion
# of them. By default the artifacts are sorted by build date and the most
# recent is kept. Verify that the correct subset was retained.
rm -rf "$archiveDir/"*
run_bob build --download no --upload -q 'many-*'
pushd $archiveDir
run_bob archive clean --fail -v 'meta.recipe == "many" LIMIT 3'
popd
test $(find $archiveDir -name '*.tgz' | wc -l) -eq 3
run_bob build --download forced --force many-07 many-06 many-05

# Do the same again with ascending sorting and a different ordering key.  The
# tricky part is that metaEnv.FUZZ is not set in all packages and such packages
# must not be counted.
rm -rf "$archiveDir/"*
run_bob build --download no --force --upload -q 'many-*'
pushd $archiveDir
run_bob archive clean --fail -v 'meta.recipe == "many" LIMIT 2 OrDeR By metaEnv.FUZZ ASC'
popd
test $(find $archiveDir -name '*.tgz' | wc -l) -eq 2
run_bob build --download forced --force many-01 many-03

# Must fail if LIMIT is zero, invalid or negative
pushd $archiveDir
expect_fail run_bob archive clean 'meta.recipe == "many" LIMIT 0'
expect_fail run_bob archive clean 'meta.recipe == "many" LIMIT -3'
expect_fail run_bob archive clean 'meta.recipe == "many" LIMIT foobar'
popd

# Build artifacts with special audit meta keys. Try to find them later.
rm -rf "$archiveDir/"* work
run_bob build --upload -M my-key=one root-alpha
run_bob build --upload -M my-key=two root-bravo
pushd $archiveDir
run_bob archive scan --fail
found1=$(run_bob archive find -n 'meta.recipe == "root" && meta.my-key == "one"')
expect_exist "$found1"
found2=$(run_bob archive find -n 'meta.recipe == "root" && meta.my-key == "two"')
expect_exist "$found2"
test "$found1" != "$found2"
popd

# Make sure invalid audit meta keys are rejected
expect_fail run_bob build -M "!nv@l1d=key" root-alpha
