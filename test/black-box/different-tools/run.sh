#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."

# This is a regression test. Deeper in the dependency tree (lib2) there is a
# dependency to a tool (foo). One root recipe defines the tool while the other
# doesn't. Bob has to detect this and must not internally reuse the packages
# because they are incompatible.

cleanup

run_bob_plain dev root1
run_bob_plain dev root2

diff -NurpZ $(run_bob_plain query-path -f {dist} root1) output/root1
diff -NurpZ $(run_bob_plain query-path -f {dist} root2) output/root2
