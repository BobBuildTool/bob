#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."

# Run a simple Python recipe. It also uses a minimal sandbox that mounts the
# full host. Just see if execution works even if $PATH is empty in the sandbox.
# There is also a bash recipe that overrides the script language back to bash.
#
# Outside the sandbox Bob uses its own interpreter to run the scripts. Inside
# the sandbox a "python" executable must be available in $PATH, though.

cleanup

run_bob dev root
RES=$(run_bob query-path -f '{dist}' --develop root)
diff -u "$RES/file.txt" recipes/file.txt

run_bob dev bash
RES=$(run_bob query-path -f '{dist}' --develop bash)
diff -u "$RES/file.txt" recipes/file.txt

run_bob dev inclusion
RES=$(run_bob query-path -f '{dist}' --develop inclusion)
diff -u "$RES/concat.txt" <(cat recipes/file.txt recipes/file2.txt)
diff -u "$RES/file.txt" recipes/file.txt

# Run the sandbox check only if the namespace feature works on this host and a
# "python" interpreter is available inside the (host mounted) sandbox.
if type -p python3 >/dev/null && "${BOB_ROOT}/bin/bob-namespace-sandbox" -C ; then
	run_bob build root
	RES=$(run_bob query-path -f '{dist}' --release root)
	diff -u "$RES/file.txt" recipes/file.txt
fi
