#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }
cleanup

# Test that corruptions of binary artifacts are detected...

# setup local archive
trap 'rm -rf "${archiveDir}"' EXIT
archiveDir=$(mktemp -d)

upload="$archiveDir/uploads"
cat >"${upload}.yaml" <<EOF
archive:
  -
    name: "local"
    backend: file
    path: "$(mangle_path "$archiveDir/artifacts")"
EOF
scratch="$archiveDir/scratch"
mkdir "$scratch"

# fill archive
run_bob build -c "$upload" --download=no --upload root
ARTIFACTS=( $(/usr/bin/find "$archiveDir/artifacts" -type f) )
test "${#ARTIFACTS[@]}" -eq 1

# save artifact for later modification
A="${ARTIFACTS[0]}"
SAVE="$archiveDir/save.tgz"
cp "$A" "$SAVE"

# Verify download of re-packed artifact still works.
rm "$A"
pushd "$scratch"
tar xf "$SAVE"
tar --pax-option bob-archive-vsn=1 -zcf "$A" meta content
popd
run_bob dev -c "$upload" --download=forced root

# Modify content. When downloading the artifact again, the corruption must be
# detected.
rm "$A"
pushd "$scratch"
tar xf "$SAVE"
echo bar > content/result.txt
tar --pax-option bob-archive-vsn=1 -zcf "$A" meta content
popd

rm -rf dev
expect_fail run_bob dev -c "$upload" --download=forced root

# Remove audit trail. Such artifacts must be rejected.
rm "$A"
pushd "$scratch"
tar xf "$SAVE"
tar --pax-option bob-archive-vsn=1 -zcf "$A" content
popd

rm -rf dev
expect_fail run_bob dev -c "$upload" --download=forced root

# Add garbage in the middle. Must fail gracefully.
len=$(stat -c %s "$SAVE")
head -c $((len - 64)) "$SAVE" > "$A"
echo -n asdf >> "$A"
tail -c 60 "$SAVE" >> "$A"

rm -rf dev
expect_fail run_bob dev -c "$upload" --download=forced root
