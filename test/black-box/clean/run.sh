#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

trap 'rm -rf "${gitDir}"' EXIT

cleanup
rm -rf default.yaml

# init a git - repo
gitDir=$(mktemp -d)
pushd "${gitDir}"
git init -b master
git config user.email "bob@bob.bob"
git config user.name test
echo "git" > test.dat
git add test.dat
git commit -m "added test"
git checkout -b topic
echo "topic" > test.dat
git commit -a -m "topic branch"
popd

cat >default.yaml <<EOF
environment:
    REPODIR : "$(mangle_path "${gitDir}")"
EOF

# build once
run_bob dev root
run_bob build root

# bob clean must not do anything by now
expect_output "" run_bob clean --develop
expect_output "" run_bob clean --release
expect_output "" run_bob clean --attic

# taint scms
echo foo > $(run_bob query-path -f '{src}' root)/foo.txt

# Build with different branch. The tained SCM is moved to the attic.
run_bob dev -c override root
run_bob build -c override root

# Taint sources in the 2nd build
releaseSrc=$(run_bob query-path -c override --release -f '{src}' root)
echo foo > $releaseSrc/foo.txt

# Now we have some attics and obsolete directories. Clean them...
run_bob clean -v -s
run_bob clean -v -s --release

# The release sources must not have been removed because they are unclean.
test -f $releaseSrc/foo.txt || { echo "cleaned?" ; exit 1; }
run_bob clean -v -f -s --release # Now force clean it
test -f $releaseSrc/foo.txt && { echo "not cleaned?" ; exit 1; }

# The attic is dirty. It must be only cleaned with -f
run_bob clean -v -s --attic
run_bob clean -v -s -f --attic
