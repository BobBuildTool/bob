#!/bin/bash -e
#
#  Test filter rules of the "import" SCM. Packages that import
#  different files from the same directory must not be rebuilt if an unrelated
#  file changes.
#
source "$(dirname "$0")/../../test-lib.sh" "../../.."

cleanup

# Copy recipes and create initial sources
prj="$(mktemp -d)"
trap 'rm -rf "$prj"' EXIT
mkdir -p "$prj/recipes" "$prj/src/patches" "$prj/src/patches/empty"
cp config.yaml "$prj/"
cp root.yaml "$prj/recipes"
echo "a1" > "$prj/src/a_defconfig"
echo "b1" > "$prj/src/b_defconfig"
echo "p1" > "$prj/src/patches/0001.patch"
echo "o1" > "$prj/src/patches/0001.patch.orig"
echo "x1" > "$prj/src/unrelated.txt"

run_bob -C "$prj" dev root-a root-b
distA="$prj/$(run_bob -C "$prj" query-path -f '{dist}' root-a)"
distB="$prj/$(run_bob -C "$prj" query-path -f '{dist}' root-b)"

# Only the selected files are imported
expect_exist "$distA/a_defconfig"
expect_not_exist "$distA/b_defconfig" "$distA/patches" "$distA/unrelated.txt"
expect_exist "$distB/b_defconfig" "$distB/patches/0001.patch"
expect_not_exist "$distB/a_defconfig" "$distB/patches/0001.patch.orig" "$distB/unrelated.txt" \
	"$distB/patches/empty"

stampA="$(cat "$distA/stamp")"
stampB="$(cat "$distB/stamp")"

# Changing a file that is imported by "b" only rebuilds "b"
echo "b2" > "$prj/src/b_defconfig"
run_bob -C "$prj" dev root-a root-b
expect_equal "$stampA" "$(cat "$distA/stamp")"
expect_not_equal "$stampB" "$(cat "$distB/stamp")"
expect_equal "b2" "$(cat "$distB/b_defconfig")"
stampB="$(cat "$distB/stamp")"

# Changing files that are not imported does not rebuild anything
echo "x2" > "$prj/src/unrelated.txt"
echo "o2" > "$prj/src/patches/0001.patch.orig"
run_bob -C "$prj" dev root-a root-b
expect_equal "$stampA" "$(cat "$distA/stamp")"
expect_equal "$stampB" "$(cat "$distB/stamp")"

# Filter rules that exclude everything are an error
rm "$prj/src/a_defconfig"
expect_fail run_bob -C "$prj" dev root-a
