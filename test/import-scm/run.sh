#!/bin/bash -e
#
#  Test various "import" SCM properties
#
. ../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

cleanup

check_result()
{
	local DATA
	read -r DATA < "$prj/output/result.txt"
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
