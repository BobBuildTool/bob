#!/bin/bash -e
#
#  Quick functionality test for git alternates
#
set -x

. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

dir=$(mktemp -d)
echo "Using scratch dir: $dir"
#trap 'rm -rf "$dir"; cleanup' EXIT
#cleanup

# Bob recipes
bob=$dir/bob
mkdir -p "$bob/recipes"
cp recipe1.yaml "$bob/recipes/t.yaml"

# Create a small repo
repo=$dir/repo.git
init_repo() {
   mkdir "$1"
   git init "$1"
   git -C "$1" config user.email "bob@bob.bob"
   git -C "$1" config user.name test

   echo "init $1" > "$1/file.txt"
   git -C "$1" add file.txt
   git -C "$1" commit -m "message"
}

init_repo $repo

# setup a mirror to be used as alternate
alternate=$dir/alternate
mkdir "$alternate"
git -C "$alternate" clone --mirror "$repo"

##
## simple alternate in recipe
##
alternate="$(mangle_path "$alternate")"
sed -i "s#<<GIT_REFERENCE>>#$alternate\/repo.git#" $bob/recipes/t.yaml
run_bob -C "$bob" dev -DURL="$repo" t -vv
src=$(run_bob -C "$bob" query-path -DURL="$repo" -f {src} t)
grep -Fxq "$alternate/repo.git/objects" $bob/$src/.git/objects/info/alternates
git -C "$bob/$src" status

####
#### Test dissociate
####
sed -i "s#dissociate: false#dissociate: true#" $bob/recipes/t.yaml
run_bob -C "$bob" dev -DURL="$repo" t -vv
src=$(run_bob -C "$bob" query-path -DURL="$repo" -f {src} t)
if [[ -e $bob/$src/.git/objects/info/alternates ]]; then
   echo "alternates file still exists"
   exit 1
fi
# in case git repack -a was not execued but the alternates file is delete
# git status fails with fatal: bad object HEAD
git -C "$bob/$src" status
rm -rf $bob

##
## Alternate matching in scmDefaults
##
bob=$dir/bob
mkdir -p "$bob/recipes"
cp recipe2.yaml "$bob/recipes/t.yaml"
cp default.yaml "$bob"
sed -i "s#<<GIT_REFERENCE>>#$alternate#" $bob/default.yaml
sed -i "s#<<GIT_URL_PATTERN>>#${repo%/repo.git}#" $bob/default.yaml
run_bob -C "$bob" dev -DURL="$repo" t
src=$(run_bob -C "$bob" query-path -DURL="$repo" -f {src} t)
grep -Fxq "$alternate/repo.git/objects" $bob/$src/.git/objects/info/alternates
rm -rf $bob/$src
