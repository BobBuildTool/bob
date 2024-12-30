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

