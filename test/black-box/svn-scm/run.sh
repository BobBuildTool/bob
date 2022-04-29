#!/bin/bash -e
#
#  Quick functionality test for Subversion
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
echo init > "$work/file.txt"

# SVN repository: contains a single repository with our file
svnroot=$dir/svnroot
mkdir "$svnroot"
svnadmin create "$svnroot"
svn mkdir -m "init" "file://$svnroot/trunk" "file://$svnroot/branches"
(
  cd "$work"
  svn co "file://$svnroot/trunk/" .
  svn add file.txt
  svn commit -m "init"
)


##
##  First check: check out initial state
##
run_bob -C "$bob" dev -DSVNROOT="$svnroot" t
result=$(run_bob -C "$bob" query-path -DSVNROOT="$svnroot" -f {dist} t)
test -n "$result"
diff -q "$bob/$result/file.txt" "$work/file.txt"


##
##  Second check: modify the file and check that it is picked up by bob
##  (incremental update works)
##
echo modif > "$work/file.txt"
(
  cd "$work"
  svn commit -m "update message" file.txt
)

run_bob -C "$bob" dev -DSVNROOT="$svnroot" t
diff -q "$bob/$result/file.txt" "$work/file.txt"


##
##  Third check: commit to a branch and checkout that
##  (change of URL works)
##
echo branch > "$work/file.txt"
(
  cd "$work"
  svn cp -m "branch message" "file://$svnroot/trunk" "file://$svnroot/branches/awesome"
  svn switch "file://$svnroot/branches/awesome"
  svn commit -m "branch message" file.txt
)

cp recipe2.yaml "$bob/recipes/t.yaml"

run_bob -C "$bob" dev -DSVNROOT="$svnroot" t
diff -q "$bob/$result/file.txt" "$work/file.txt"
