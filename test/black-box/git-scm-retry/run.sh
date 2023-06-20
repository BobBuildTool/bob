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
cp t.yaml "$bob/recipes"

# Directory to play in
work=$dir/_work
mkdir "$work"
git init "$work"
git -C "$work" config user.email "bob@bob.bob"
git -C "$work" config user.name test

echo init > "$work/file.txt"
git -C "$work" add file.txt
git -C "$work" commit -m "message"


##
## Run bob in background. The first checkout will fail as the repo is
## not in place. While the retry 3s timer is running move the repo to
## the correct locationt to make git happy in the next round.
##
run_bob -C "$bob" dev -DURL="$dir/work" t &
pid=$!
sleep 1
mv $dir/_work $dir/work

wait $pid

test -n "$?"
