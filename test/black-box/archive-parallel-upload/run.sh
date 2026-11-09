#!/bin/bash -e
#
# Verify that uploads in parallel builds do not block dependent steps. The
# upload command waits for the build of the root package, which can only run
# after the "dep" package was built. If the upload of "dep" blocked the build
# of "root" the upload would time out and fail the build.
#
source "$(dirname "$0")/../../test-lib.sh" "../../.."
cleanup

trap 'rm -rf "${tmpDir}"' EXIT
tmpDir=$(mktemp -d)
archiveDir="$(mangle_path "$tmpDir/archive")"
marker="$(mangle_path "$tmpDir/marker")"

cat >"$tmpDir/blocking.yaml" <<EOF
archive:
    backend: shell
    upload: |
        for i in \$(seq 300) ; do
            [[ -e "$marker" ]] && break
            sleep 0.1
        done
        [[ -e "$marker" ]]
        mkdir -p "$archiveDir/\${BOB_REMOTE_ARTIFACT%/*}"
        cp "\$BOB_LOCAL_ARTIFACT" "$archiveDir/\$BOB_REMOTE_ARTIFACT"
EOF

cat >"$tmpDir/failing.yaml" <<EOF
archive:
    backend: shell
    upload: "false"
EOF

# Upload of "dep" runs while "root" is built. Both packages must be uploaded
# in the end.
run_bob dev -c "$tmpDir/blocking" --upload -j4 root -DMARKER="$marker"
expect_equal 2 "$(/usr/bin/find "$tmpDir/archive" -type f -name '*.tgz' | wc -l)"

# Failing background uploads still fail the build
expect_fail run_bob dev -c "$tmpDir/failing" --upload -j4 -f root -DMARKER="$marker"
expect_fail run_bob dev -c "$tmpDir/failing" --upload -j4 -f -k root -DMARKER="$marker"
