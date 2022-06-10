#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }
cleanup

trap 'rm -rf "${tmpDir}"' EXIT
tmpDir=$(mktemp -d)
shareDir="$tmpDir/shared"
archiveDir="$tmpDir/artifacts"
cfg="$tmpDir/cfg"

writeCfg()
{
cat >"${cfg}.yaml" <<EOF
share:
    path: "$(mangle_path "$shareDir")"
archive:
    backend: file
    path: "$(mangle_path "$archiveDir")"
EOF
}

writeCfg

# Test the various aspects of shared packages

# Prohibiting shared package install does what it sais
run_bob dev -c $cfg root --no-install
expect_not_exist "$shareDir"

# Building without audit trail does not install the shared package
cleanup
run_bob dev -c $cfg root --install --no-audit
expect_not_exist "$shareDir"

# Initial build, upload to binary artifact cache and install shared package
cleanup
run_bob dev -c $cfg root --upload --download no
expect_exist "$shareDir/1e/64/44e72b94e7306c8e1555f31cfec30ffe981d-3/workspace/result.txt"
test -L dev/dist/root/1/workspace
oldLink="$(readlink dev/dist/root/1/workspace)"

# Running again does nothing and keeps the sharing
run_bob dev -c $cfg root --download no
test -L dev/dist/root/1/workspace
test "$(readlink dev/dist/root/1/workspace)" = "$oldLink"

# Building the same package from somewhere else uses the shared package.
mkdir -p "$tmpDir/project2"
cp -r config.yaml recipes/ "$tmpDir/project2"
pushd "$tmpDir/project2"
run_bob dev -c $cfg root --download no
expect_not_exist dev/build/root/1/workspace
test -L dev/dist/root/1/workspace
popd

# Clean build using the shared package.
cleanup
run_bob dev -c $cfg root --download no
expect_exist dev/dist/root/1/workspace
expect_not_exist dev/build/root/1/workspace

# Move the shared location. The shared package is adapted but not built.
newShareDir="$tmpDir/moved"
mv "$shareDir" "$newShareDir"
shareDir="$newShareDir"
writeCfg
run_bob dev -c $cfg root --download no
expect_exist dev/dist/root/1/workspace
test -L dev/dist/root/1/workspace
expect_not_exist dev/build/root/1/workspace

# Move the shared location. The shared package is rebuilt and installed again
shareDir="$tmpDir/other"
writeCfg
run_bob dev -c $cfg root --download no
expect_exist dev/dist/root/1/workspace
expect_exist dev/build/root/1/workspace

# Prohibiting shared packages removes the sharing and builds the artifact again
rm -rf dev/build
test -L dev/dist/root/1/workspace
run_bob dev -c $cfg root --download no --no-shared
expect_exist dev/dist/root/1/workspace
test -d dev/build/root/1/workspace

# A download can also install to a shared location
rm -rf "$shareDir"
cleanup
run_bob dev -c $cfg root --download yes
expect_exist "$shareDir/1e/64/44e72b94e7306c8e1555f31cfec30ffe981d-3/workspace/result.txt"
test -L dev/dist/root/1/workspace

# One can still install shared packages without using them. The workspace will
# still contain files.
rm -rf "$shareDir"
cleanup
run_bob dev -c $cfg root --download no --no-shared --install
expect_exist "$shareDir/1e/64/44e72b94e7306c8e1555f31cfec30ffe981d-3/workspace/result.txt"
test -d dev/dist/root/1/workspace

# Re-building with shared packages will remove existing workspaces and use
# the available shared package.
cleanup
run_bob dev root --download no
test -d dev/dist/root/1/workspace
run_bob dev -c $cfg root --download no
test -L dev/dist/root/1/workspace

########################

cleanup

# An invalid result hash is rejected
echo '{"hash": "12345678", "size": 7}' > "$shareDir/1e/64/44e72b94e7306c8e1555f31cfec30ffe981d-3/pkg.json"
expect_fail run_bob dev -c $cfg root --download no --no-install

# A broken meta info handled
echo '{"size": 7}' > "$shareDir/1e/64/44e72b94e7306c8e1555f31cfec30ffe981d-3/pkg.json"
expect_fail run_bob dev -c $cfg root --download no --no-install

# An invalid meta info handled
echo 'garbage' > "$shareDir/1e/64/44e72b94e7306c8e1555f31cfec30ffe981d-3/pkg.json"
expect_fail run_bob dev -c $cfg root --download no --no-install

# Installing to invalid location fails gracefully
rm -rf "$shareDir"
touch "$shareDir"
expect_fail run_bob dev -c $cfg root --download no --install
