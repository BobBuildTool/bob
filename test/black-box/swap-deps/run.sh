#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."
cleanup

# In develop build mode the directories are not 100% stable. If the dependency
# tree is changed the directories of the direct dependencies of a build step
# might change. This must lead to a clean build because many tools (e.g. cmake)
# assume stable input directories on incremental builds.

run_bob dev root -DORDER=ab
V1="$(run_bob query-path --develop -DORDER=ab root/variant-a/dep)"
rm -f .bob-dev-dirs.sqlite3
run_bob dev root -DORDER=ba
V2="$(run_bob query-path --develop -DORDER=ba root/variant-a/dep)"

if [[ $V1 = $V2 ]] ; then
   echo "Directories did not change as exptected" >&2
   exit 1
fi
