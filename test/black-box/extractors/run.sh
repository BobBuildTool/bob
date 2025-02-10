#!/bin/bash -e

. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }
cleanup

INPUT=$PWD/input
test "${INPUT:0:1}" = "/"
INPUT="$(mangle_path "$INPUT")"

IS_POSIX="false"
if is_posix ; then
  IS_POSIX="true"
fi

# Build and fetch result path
run_bob dev -DINPUT_FILES="${INPUT}" -DIS_POSIX="$IS_POSIX" extract_test

check_files() {
  expect_not_exist "dev/src/extract_test/1/workspace/$1/.test.${2:-$1}.extracted"
  expect_not_exist "dev/src/extract_test/1/workspace/$1/test.${2:-$1}"
  expect_exist "dev/src/extract_test/1/download/$1/.test.${2:-$1}.extracted"
  expect_exist "dev/src/extract_test/1/download/$1/test.${2:-$1}"
}

check_files "tar" "tgz"
check_files "zip"

if is_posix; then
  check_files "gzip" "dat.gz"
  check_files "xz" "dat.xz"
  check_files "7z"
fi
