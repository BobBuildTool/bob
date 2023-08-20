#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

# Run a simple PowerShell recipe. It also uses a minimal sandbox that mounts
# the full host. Just see if exeuction works even if $PATH is empty in the
# sandbox. There is also a bash recipe that overrides the script language back
# to bash.

type -p pwsh >/dev/null || type -p powershell >/dev/null || skip

cleanup
run_bob dev root
RES=$(run_bob query-path -f '{dist}' --develop root)
diff -u "$RES/file.txt" recipes/file.txt

cleanup
run_bob dev bash
RES=$(run_bob query-path -f '{dist}' --develop bash)
diff -u "$RES/file.txt" recipes/file.txt

# Run the sandbox check only if namespace feature works on this host.
if "${BOB_ROOT}/bin/bob-namespace-sandbox" -C ; then
	run_bob build root
	RES=$(run_bob query-path -f '{dist}' --release root)
	diff -u "$RES/file.txt" recipes/file.txt
fi
