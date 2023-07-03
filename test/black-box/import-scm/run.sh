#!/bin/bash -e
#
#  Test various "import" SCM properties
#
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

cleanup

check_result()
{
	local DATA
	read -r DATA < "$prj/output/${2:-.}/result.txt"
	[[ "$1" = "$DATA" ]] || { echo "Mismatch: $1 <> $DATA" ; exit 1; }
}

# Copy recipes and create initial sources
prj="$(mktemp -d)"
trap 'rm -rf "$prj"' EXIT
mkdir -p "$prj/recipes" "$prj/src"
cp config.yaml "$prj/"
cp root.yaml "$prj/recipes"
echo "1" > "$prj/src/result.txt"

# Just build
run_bob -C "$prj" dev --destination output root
check_result "1"

# Check that only newer files are copied
echo "2" > "$prj/src/result.txt"
touch --date="-1day" "$prj/src/result.txt"
run_bob -C "$prj" dev --destination output root
check_result "1"
touch "$prj/src/result.txt"
run_bob -C "$prj" dev --destination output root
check_result "2"

# The import SCM is still updated on build-only builds
echo "3" > "$prj/src/result.txt"
run_bob -C "$prj" dev --destination output root --build-only
check_result "3"

# The import SCM is even updated when the checkoutScript changes!
# But the checkoutScript itself must not run.
echo 'checkoutScript: "rm -f result.txt"' >>"$prj/recipes/root.yaml"
echo "4" > "$prj/src/result.txt"
run_bob -C "$prj" dev --destination output root --build-only
check_result "4"

# But when changing the import SCM itself, Bob will refuse to update the
# workspace on build-only builds. This can only be rectified by running without
# --build-only.
echo "5" > "$prj/src/result.txt"
run_bob -C "$prj" dev --destination output root -DIMPORT_DIR=sub --build-only
check_result "4"
run_bob -C "$prj" dev --destination output root -DIMPORT_DIR=sub
check_result "5" "sub"
