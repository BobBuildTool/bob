#!/bin/bash -e
#
# Check the various inline upgrade options of git. Will also provoke failures
# to verify that the attic mode is still triggered.
#
. ../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

git_dir1=$(mktemp -d)
git_dir2=$(mktemp -d)
git_submod=$(mktemp -d)
trap 'rm -rf "$git_dir1" "$git_dir2" "$git_submod"' EXIT
cleanup

# Prepare git repositories

pushd "$git_submod"
git init .
git config user.email "bob@bob.bob"
git config user.name test
echo sub > sub.txt
git add sub.txt
git commit -m import
popd

pushd "$git_dir1"
git init .
git config user.email "bob@bob.bob"
git config user.name test
echo "hello world" > test.txt
git add test.txt
git submodule add "$git_submod" submod
git commit -m "first commit"
git tag -a -m "First Tag" tag1
git checkout -b foobar
d1_c1=$(git rev-parse HEAD)
echo "changed" > test.txt
git commit -a -m "second commit"
git tag -a -m "Second Tag" tag2
d1_c2=$(git rev-parse HEAD)
popd

pushd "$git_dir2"
git init .
git config user.email "bob@bob.bob"
git config user.name test
echo "hello bob" > bob.txt
git add bob.txt
git commit -m "first commit"
d2_c1=$(git rev-parse HEAD)
popd


# First a simple checkout. We put a canary there to detect attic moves.
run_bob dev -DSCM_DIR="$git_dir1" -DSCM_REV="refs/heads/master" root
expect_output "hello world" cat dev/src/root/1/workspace/test.txt
echo canary > dev/src/root/1/workspace/canary.txt

# Change branch
run_bob dev -DSCM_DIR="$git_dir1" -DSCM_REV="refs/heads/foobar" root
expect_output "changed" cat dev/src/root/1/workspace/test.txt
expect_exist dev/src/root/1/workspace/canary.txt

# Change back
run_bob dev -DSCM_DIR="$git_dir1" -DSCM_REV="refs/heads/master" root
expect_output "hello world" cat dev/src/root/1/workspace/test.txt
expect_exist dev/src/root/1/workspace/canary.txt

# Enabling submodules on branch is ok
run_bob dev -c submodules -DSCM_DIR="$git_dir1" -DSCM_REV="refs/heads/master" root
expect_exist dev/src/root/1/workspace/canary.txt
expect_exist dev/src/root/1/workspace/submod/sub.txt

# But disabling submodules on branch must trigger an attic move
run_bob dev -DSCM_DIR="$git_dir1" -DSCM_REV="refs/heads/master" root
expect_not_exist dev/src/root/1/workspace/canary.txt
expect_not_exist dev/src/root/1/workspace/submod/sub.txt

# Change repository but keep branch. This must move the dir into the attic
# because they do not share a common history and the branch cannot be
# forwarded.
echo canary > dev/src/root/1/workspace/canary.txt
run_bob dev -DSCM_DIR="$git_dir2" -DSCM_REV="refs/heads/master" root -j
expect_not_exist dev/src/root/1/workspace/test.txt
expect_output "hello bob" cat dev/src/root/1/workspace/bob.txt
expect_not_exist dev/src/root/1/workspace/canary.txt
expect_exist dev/src/root/1/workspace/bob.txt

# Revert back to 1st repository and checkout tag. This should succeed as inline
# upgrade because this is always a hard checkout.
echo canary > dev/src/root/1/workspace/canary.txt
run_bob dev -DSCM_DIR="$git_dir1" -DSCM_REV="refs/tags/tag2" root
expect_output "changed" cat dev/src/root/1/workspace/test.txt
expect_not_exist dev/src/root/1/workspace/bob.txt
expect_exist dev/src/root/1/workspace/canary.txt

# Enabling submodules on tags is ok
run_bob dev -c submodules -DSCM_DIR="$git_dir1" -DSCM_REV="refs/tags/tag2" root
expect_exist dev/src/root/1/workspace/canary.txt
expect_exist dev/src/root/1/workspace/submod/sub.txt

# But disabling submodules on tags must trigger an attic move
run_bob dev -DSCM_DIR="$git_dir1" -DSCM_REV="refs/tags/tag2" root
expect_not_exist dev/src/root/1/workspace/canary.txt
expect_not_exist dev/src/root/1/workspace/submod/sub.txt

# Trying to trigger an inline upgrade for dirty data will fail and move to attic.
echo canary > dev/src/root/1/workspace/canary.txt
echo taint > dev/src/root/1/workspace/test.txt
expect_exist dev/src/root/1/workspace/canary.txt
run_bob dev -DSCM_DIR="$git_dir1" -DSCM_REV="$d1_c1" root
expect_not_exist dev/src/root/1/workspace/canary.txt
expect_output "hello world" cat dev/src/root/1/workspace/test.txt
