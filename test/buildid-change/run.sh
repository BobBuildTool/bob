#!/bin/bash -e
. ../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }
cleanup

REPO="$(mktemp -d)"
trap 'rm -rf "$REPO"' EXIT

# create empty git repo and archive
pushd "$REPO"
git init --bare test.git
mkdir archive
popd

# fill git repo
D="$(mktemp -d)"
pushd "$D"
git init .
echo "first" > first.txt
git add first.txt
git commit -m '1st commit'
git remote add origin "$REPO/test.git"
git push origin HEAD
popd
rm -rf "$D"

# create binary archive config
cat >repo.yaml <<EOF
archive:
    backend: file
    path: "$REPO/archive"
EOF

# initial run to upload common package
run_bob build -DREPO="$REPO" --download=yes --upload root/right/common

# there should be exactly one artifact
shopt -s nullglob
A=( "$REPO"/archive/*/*/*.tgz )
[[ ${#A[@]} -eq 1 ]] || exit 1
shopt -u nullglob

# next run to upload "right" package
run_bob build -DREPO="$REPO" --download=yes --upload root/right

# Remove workspace and delete "common" package artifact. Live-build-id
# predictions are kept.
rm -rf work
rm "${A[0]}"

# Build-again, this time with different sources than predicted.
export APPLY_CHANGE=1
run_bob build -DREPO="$REPO" --download=yes root
unset APPLY_CHANGE

# Validate result
diff -Nurp $(run_bob query-path --release -f '{dist}'  -DREPO="$REPO" root) output
