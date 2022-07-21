#!/bin/bash -e
#
#  Quick functionality test for CVS
#
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

# FIXME: does not really work on native windows
if is_win32 ; then
	skip
fi

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

# CVS repository: contains a single repository "mod" with our file
cvs_root=$dir/cvs_root
mkdir "$cvs_root"
cvs -d "$(native_path $cvs_root)" init
(
  cd "$work"
  cvs -d "$(native_path $cvs_root)" import -m "message" mod vendor release
  cvs -d "$(native_path $cvs_root)" co -d . mod
)
  

##
##  First check: check out initial state
##
run_bob -C "$bob" dev -DCVSROOT="$(native_path $cvs_root)" t
result=$(run_bob -C "$bob" query-path -DCVSROOT="$(native_path $cvs_root)" -f {dist} t)
test -n "$result"
diff -q "$bob/$result/file.txt" "$work/file.txt"


##
##  Second check: modify the file and check that it is picked up by bob
##  (incremental update works)
##
echo modif > "$work/file.txt"
(
  cd "$work"
  cvs ci -m "update message" file.txt
)

run_bob -C "$bob"  dev -DCVSROOT="$(native_path $cvs_root)" t -vv
diff -q "$bob/$result/file.txt" "$work/file.txt"


##
##  Third check: commit to a branch and checkout that
##  (change of branch/tag works)
##
echo branch > "$work/file.txt"
(
  cd "$work"
  cvs tag -b my_branch
  cvs up  -r my_branch
  cvs ci -m "branch message" file.txt
)

cp recipe2.yaml "$bob/recipes/t.yaml"

run_bob -C "$bob" dev -DCVSROOT="$(native_path $cvs_root)" t
diff -q "$bob/$result/file.txt" "$work/file.txt"
