#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }
cleanup

run_with_source()
{
	run_bob "$@" -DSOURCE_FILE="$(file_url "$SRC")" -DSOURCE_HASH="$(sha1sum "$SRC" | cut -d ' ' -f1)"
}

compare_result()
{
   local d="$(run_with_source query-path -f '{dist}' root)"
   local result generation
   read -r result < "$d/result.txt"
   read -r generation < "$d/generation.txt"

   if [[ "$result" != "$1" ]] ; then
      echo "Mismatched result: $result <> $1"
      exit 1
   fi
   if [[ "$generation" != "$2" ]] ; then
      echo "Mismatched generation: $generation <> $2"
      exit 1
   fi

   return 0
}

# Test that --build-only does not needlessly rebuild if just the checkout was
# changed. On the other hand test that we make a clean build if we then run
# again without --build-only where the new sources are fetched.

# Checkout "original" sources.
(
   SRC="$(mktemp)"
   trap 'rm -f "$SRC"' EXIT
   echo "foo" > "$SRC"

   run_with_source dev root -DGENERATION=1
   compare_result "foo" "1"
)

# Change sources and run again.
(
   SRC="$(mktemp)"
   trap 'rm -f "$SRC"' EXIT
   echo "bar" > "$SRC"

   # First run with --build-only. Must not rebuild.
   run_with_source dev root -DGENERATION=2 --build-only
   compare_result "foo" "1"

   # Now run again with checkout. Make sure we rebuild clean.
   run_with_source dev root -DGENERATION=3
   compare_result "bar" "3"

   # Running again should make no difference.
   run_with_source dev root -DGENERATION=4
   compare_result "bar" "3"
)
