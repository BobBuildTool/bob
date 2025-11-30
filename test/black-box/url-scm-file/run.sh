#!/bin/bash -e
#
#  Litmus test for "url" SCM with files.
#  Verify that a file name in place of an URL works.
#
source "$(dirname "$0")/../../test-lib.sh" "../../.."
cleanup

# Source file. If shell didn't give us an absolute file name, bail out early.
file="$PWD/file.txt"
test "${file:0:1}" = "/"

# Build and fetch result path
file="$(mangle_path "$file")"
run_bob dev -DURL="$file" root
path=$(run_bob query-path -DURL="$file" -f {dist} root)
test -n "$path"

# Build result must contain a copy of the file.
diff -q "$path/file.txt" "$file"
