#!/bin/bash -e
#
#  Test recipeRelative "import" SCM property
#
source "$(dirname "$0")/../../test-lib.sh" "../../.."

cleanup

# First try in-tree build
run_bob dev sub::root
diff -Nrq recipes/sub/data dev/dist/sub/root/1/workspace

# Out of tree builds should work as well
build="$(mktemp -d)"
trap 'rm -rf "$build"' EXIT
run_bob init . "$build"
run_bob -C "$build" dev sub::root
diff -Nrq recipes/sub/data "$build/dev/dist/sub/root/1/workspace"
