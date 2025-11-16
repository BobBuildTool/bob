#!/bin/bash -e
#
# Verify checkouts and updates of nested SCMs.

source "$(dirname "$0")/../../test-lib.sh" "../../.."

git_dir1=$(mktemp -d)
git_dir2=$(mktemp -d)
trap 'rm -rf "$git_dir1" "$git_dir2"' EXIT
cleanup

# Prepare git repositories

pushd "$git_dir1"
git init -b master .
git config user.email "bob@bob.bob"
git config user.name test
echo "commit-1" > git1.txt
git add git1.txt
git commit -m "initial commit"
git tag -a -m "First Tag" tag1
echo "commit-2" > git1.txt
git commit -a -m "second commit"
git tag -a -m "Second Tag" tag2
popd

pushd "$git_dir2"
git init -b master .
git config user.email "bob@bob.bob"
git config user.name test
echo "commit-1" > git2.txt
git add git2.txt
git commit -m "first commit"
git tag -a -m "First Tag" tag1
echo "commit-2" > git2.txt
git commit -a -m "second commit"
git tag -a -m "Second Tag" tag2
popd


# First a simple checkout. We put a canary there to detect attic moves.
run_bob dev -DGIT_1_DIR="$git_dir1" -DGIT_2_DIR="$git_dir2" root
expect_output "commit-2" cat dev/src/root/1/workspace/foo/git1.txt
echo canary > dev/src/root/1/workspace/foo/canary.txt
expect_output "commit-2" cat dev/src/root/1/workspace/foo/bar/git2.txt
echo canary > dev/src/root/1/workspace/foo/bar/canary.txt

# Change tag on nested SCM
run_bob dev -DGIT_1_DIR="$git_dir1" -DGIT_2_DIR="$git_dir2" \
	-DGIT_2_REV="refs/tags/tag1" root
expect_exist dev/src/root/1/workspace/foo/canary.txt
expect_exist dev/src/root/1/workspace/foo/bar/canary.txt
expect_output "commit-2" cat dev/src/root/1/workspace/foo/git1.txt
expect_output "commit-1" cat dev/src/root/1/workspace/foo/bar/git2.txt

# Change tag on upper SCM
run_bob dev -DGIT_1_DIR="$git_dir1" -DGIT_2_DIR="$git_dir2" \
	-DGIT_1_REV="refs/tags/tag1" \
	-DGIT_2_REV="refs/tags/tag1" root
expect_exist dev/src/root/1/workspace/foo/canary.txt
expect_exist dev/src/root/1/workspace/foo/bar/canary.txt
expect_output "commit-1" cat dev/src/root/1/workspace/foo/git1.txt
expect_output "commit-1" cat dev/src/root/1/workspace/foo/bar/git2.txt

# Remove upper SCM. The upper SCM and the nested one are both moved to the
# attic and can be found there. The "nested" SCM is then checked out again.
run_bob dev -DGIT_1_DIR="$git_dir1" -DGIT_1_ENABLE=0 -DGIT_2_DIR="$git_dir2" root
expect_not_exist dev/src/root/1/workspace/foo/canary.txt
expect_not_exist dev/src/root/1/workspace/foo/git1.txt
expect_not_exist dev/src/root/1/workspace/foo/bar/canary.txt
expect_output "commit-2" cat dev/src/root/1/workspace/foo/bar/git2.txt
expect_exist dev/src/root/1/attic
expect_exist dev/src/root/1/attic/*foo
expect_exist dev/src/root/1/attic/*foo/bar

status=$(run_bob status -DGIT_1_DIR="$git_dir1" -DGIT_1_ENABLE=0 -DGIT_2_DIR="$git_dir2" \
		--attic --show-clean)
# On Windows "bob status" will output native paths. Needs to be substituted
# before comparison.
[[ ${status//\\//} == *dev/src/root/1/attic/*foo* ]]
[[ ${status//\\//} == *dev/src/root/1/attic/*foo/bar ]]
