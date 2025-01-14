#!/bin/bash -e
#
# Verify that digests can be added/removed/modified without re-downloading a
# file.
#
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }
cleanup

tempdir=$(mktemp -d)
trap 'rm -rf "${tempdir}"' EXIT

url="$(mangle_path "$(realpath file.txt)")"
url2="$(mangle_path "$(realpath file2.txt)")"

tar -cvzf $tempdir/file.tgz file.txt
tar -cvzf $tempdir/file2.tgz file2.txt

# Build and fetch result path
run_bob dev root -DURL="$url"
path=$(run_bob query-path -DURL="$url" -f {src} root)

# Add wrong sha1sum. Will fail build but the workspace is not moved to attic.
echo "canary" > "$path/canary.txt"
expect_fail run_bob dev root -DURL="$url" -c incorrect-sha1
expect_exist  "$path/file.txt"
expect_exist  "$path/canary.txt"

# Moving to right sha1sum makes the build succeed and keeps the workspace intact
run_bob dev root -DURL="$url" -c correct-sha1
expect_exist  "$path/canary.txt"

# Adding sha256 makes no difference
run_bob dev root -DURL="$url" -c correct-sha1-sha256
expect_exist  "$path/canary.txt"

# Removing hash sums is a no-op
run_bob dev root -DURL="$url"
expect_exist  "$path/canary.txt"

# Changing the URL will move old workspace to attic
run_bob dev root -DURL="$url2"
expect_not_exist  "$path/file.txt"
expect_not_exist  "$path/canary.txt"
diff -q "$path/file2.txt" file2.txt

cleanup
run_bob dev root -DURL="$tempdir/file.tgz"
expect_exist $path/../download/file.tgz

run_bob dev root -DURL="$tempdir/file2.tgz"
expect_exist $path/../download/file2.tgz
expect_not_exist $path/../download/file.tgz
