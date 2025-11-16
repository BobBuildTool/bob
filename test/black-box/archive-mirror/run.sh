#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."
cleanup

# Test caching of downloaded artifacts

# setup local archive
trap 'rm -rf "${archiveDir}"' EXIT
archiveDir=$(mktemp -d)

upload="$archiveDir/uploads"
cat >"${upload}.yaml" <<EOF
archive:
  -
    name: "local"
    backend: file
    path: "$(mangle_path "$archiveDir/source")"
EOF

# Three different mirrors where the 3rd one silently fails.
mirror="$archiveDir/mirror"
cat >"${mirror}.yaml" <<EOF
archive:
  -
    name: "source"
    flags: [download]
    backend: file
    path: "$(mangle_path "$archiveDir/source")"
  -
    name: "mirror1"
    flags: [download, cache]
    backend: file
    path: "$(mangle_path "$archiveDir/mirror1")"
  -
    name: "shell1"
    flags: [download, cache]
    backend: shell
    download: "cp \\"$archiveDir/mirror2/\$BOB_REMOTE_ARTIFACT\\" \\"\$BOB_LOCAL_ARTIFACT\\""
    upload: "mkdir -p \\"$archiveDir/mirror2/\${BOB_REMOTE_ARTIFACT%/*}\\" && cp -n \\"\$BOB_LOCAL_ARTIFACT\\" \\"$archiveDir/mirror2/\$BOB_REMOTE_ARTIFACT\\""
  -
    name: "shell2"
    flags: [download, cache, nofail]
    backend: shell
    upload: "false"
EOF

# A mirror that fails on upload
failMirror="$archiveDir/failMirror"
cat >"${failMirror}.yaml" <<EOF
archive:
  -
    flags: [download]
    backend: file
    path: "$(mangle_path "$archiveDir/source")"
  -
    flags: [cache]
    backend: shell
    upload: "false"
EOF

# fill archive
run_bob build -c "$upload" --download=no --upload root
test -n "$(/usr/bin/find "$archiveDir/source" -type f)"

# download with mirror
run_bob dev -c "$mirror" --download=yes root

# make sure all archives have the same content by now
diff -u <(cd "$archiveDir/source" ; /usr/bin/find -type f | sort) <(cd "$archiveDir/mirror1" ; /usr/bin/find -type f | sort)
diff -u <(cd "$archiveDir/source" ; /usr/bin/find -type f | sort) <(cd "$archiveDir/mirror2" ; /usr/bin/find -type f | sort)

# failing mirrors must fail the build
rm -rf dev
expect_fail run_bob dev -c "$failMirror" --download=yes root
