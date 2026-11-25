#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."

# The audit trails of the dependencies must be accessible from within the
# step scripts via BOB_DEP_AUDITS/BOB_ALL_AUDITS. This must work with and
# without a sandbox. In a sandbox the audit files are not part of the
# mounted workspaces and have to be mounted explicitly.

cleanup

# Without sandbox
run_bob dev root
expect_exist dev/dist/root/1/workspace/audit-check.txt

if is_posix ; then
	# Needs working sandbox
	"${BOB_ROOT}/bin/bob-namespace-sandbox" -C || skip

	# Slim sandbox without stable paths. The audit files are visible through
	# the read-only host mount.
	cleanup
	run_bob dev root --slim-sandbox
	expect_exist dev/dist/root/1/workspace/audit-check.txt

	# Slim sandbox with stable paths. The workspaces are mounted at
	# /bob/<variant-id>/workspace and the audit files must be mounted next
	# to them.
	cleanup
	run_bob dev root --strict-sandbox
	expect_exist dev/dist/root/1/workspace/audit-check.txt
fi
