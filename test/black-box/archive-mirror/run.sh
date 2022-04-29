#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }
cleanup

# Test caching of downloaded artifacts

# setup local archive
trap 'rm -rf "${archiveDir}"' EXIT
archiveDir=$(mktemp -d)

upload="$archiveDir/uploads"
cat >"${upload}.yaml" <<EOF
archive:
  -
    backend: file
    path: "$archiveDir/source"
EOF

# Three different mirrors where the 3rd one silently fails.
mirror="$archiveDir/mirror"
cat >"${mirror}.yaml" <<EOF
archive:
  -
    flags: [download]
    backend: file
    path: "$archiveDir/source"
  -
    flags: [download, cache]
    backend: file
    path: "$archiveDir/mirror1"
  -
    flags: [download, cache]
    backend: shell
    download: "cp \\"$archiveDir/mirror2/\$BOB_REMOTE_ARTIFACT\\" \\"\$BOB_LOCAL_ARTIFACT\\""
    upload: "mkdir -p \\"$archiveDir/mirror2/\${BOB_REMOTE_ARTIFACT%/*}\\" && cp -n \\"\$BOB_LOCAL_ARTIFACT\\" \\"$archiveDir/mirror2/\$BOB_REMOTE_ARTIFACT\\""
  -
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
    path: "$archiveDir/source"
  -
    flags: [cache]
    backend: shell
    upload: "false"
EOF

# fill archive
run_bob build -c "$upload" --download=no --upload root
test -n "$(find "$archiveDir/source" -type f)"

# download with mirror
run_bob dev -c "$mirror" --download=yes root

# make sure all archives have the same content by now
diff -u <(cd "$archiveDir/source" ; find -type f | sort) <(cd "$archiveDir/mirror1" ; find -type f | sort)
diff -u <(cd "$archiveDir/source" ; find -type f | sort) <(cd "$archiveDir/mirror2" ; find -type f | sort)

# failing mirrors must fail the build
rm -rf dev
expect_fail run_bob dev -c "$failMirror" --download=yes root
