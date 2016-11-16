#!/bin/bash -e
. ../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

#
#  Blackbox tests for "query-path" command
#

# Clean
cleanup

#
#  Test path generation in release mode
#

# Must report no path for all usages because we have not built.
test -z "$(run_bob query-path --release root)"
test -z "$(run_bob query-path --release root/interm1/child)"
test -z "$(run_bob query-path --release root/interm2/child)"

# Perform a full release build. Must report paths for everything.
run_bob build root
set -- $(run_bob query-path --release root)
test "$1" = "root"
test "$2" = "work/root/dist/1/workspace"

# It is an implementation detail which workspace is which child, so just test we get something.
test -n "$(run_bob query-path --release root/interm1/child)"
test -n "$(run_bob query-path --release root/interm2/child)"


#
#  Test path generation in dev mode
#

# Must report no path for all usages because we have not built.
test -z "$(run_bob query-path --dev root)"
test -z "$(run_bob query-path --dev root/interm1/child)"
test -z "$(run_bob query-path --dev root/interm2/child)"

# Build everything
run_bob dev root
set -- $(run_bob query-path --dev root)
test "$1" = "root"
test "$2" = "dev/dist/root/1/workspace"

# 'query-path --dev' is the default
PACKAGES="root root/interm1/child root/interm2/child"
RESULT=$(run_bob query-path --dev $PACKAGES)
test "$RESULT" = "$(run_bob query-path $PACKAGES)"

# No matter in which order we build, we must get the same result.
for i in $PACKAGES; do
    rm -rf work dev .bob-*
    run_bob dev $i
    run_bob dev root
    test "$(run_bob query-path --dev $PACKAGES)" = "$RESULT"
done


#
#  Test formatting
#
run_bob build root
test "$(run_bob query-path --release -f '{name}' root)" = "root"
test "$(run_bob query-path --release -f '{name}{name}' root)" = "rootroot"
test "$(run_bob query-path --release -f 'X{name}X' root)" = "XrootX"
test "$(run_bob query-path --release -f '{dist}' root)" = "work/root/dist/1/workspace"
test "$(run_bob query-path --release -f '{build}' root)" = "work/root/build/1/workspace"

# We don't have source, so these report empty
test -z "$(run_bob query-path --release -f '{src}' root)"
test -z "$(run_bob query-path --release -f 'X{src}X' root)"
test -z "$(run_bob query-path --release -f '{src} {build}' root)"
test -z "$(run_bob query-path --release -f '{build} {src}' root)"

echo "Test ok"
