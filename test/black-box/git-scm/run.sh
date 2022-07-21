#!/bin/bash -e
#
#  Quick functionality test for git
#
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

dir=$(mktemp -d)
echo "Using scratch dir: $dir"
trap 'rm -rf "$dir"; cleanup' EXIT
cleanup

# Bob recipes
bob=$dir/bob
mkdir -p "$bob/recipes"
cp recipe1.yaml "$bob/recipes/t.yaml"

# Directory to play in
work=$dir/work
mkdir "$work"
git init "$work"
git -C "$work" config user.email "bob@bob.bob"
git -C "$work" config user.name test

echo init > "$work/file.txt"
git -C "$work" add file.txt
git -C "$work" commit -m "message"


##
##  First check: check out initial state
##
run_bob -C "$bob" dev -DURL="$work" t
result=$(run_bob -C "$bob" query-path -DURL="$work" -f {dist} t)
test -n "$result"
diff -qZ "$bob/$result/file.txt" "$work/file.txt"


##
##  Second check: modify the file and check that it is picked up by bob
##  (incremental update works)
##
echo modif > "$work/file.txt"
git -C "$work" commit -m "update message" file.txt

run_bob -C "$bob"  dev -DURL="$work" t
diff -qZ "$bob/$result/file.txt" "$work/file.txt"


##
##  Third check: commit to a branch and checkout that
##  (change of branch works)
##
echo branch > "$work/file.txt"
git -C "$work" checkout -b awesome_feature
git -C "$work" commit -m "branch message" file.txt

cp recipe2.yaml "$bob/recipes/t.yaml"

run_bob -C "$bob" dev -DURL="$work" t
diff -qZ "$bob/$result/file.txt" "$work/file.txt"
