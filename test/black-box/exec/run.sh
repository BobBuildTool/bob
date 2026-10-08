#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."

# Exercise the "exec" invocation mode, i.e. "build.sh exec <command>...". It
# must work for every supported scripting language (bash, python, PowerShell)
# and regardless of the sandboxing mode (none, slim or full/fat sandbox with
# an image).

HAVE_SANDBOX=0
"${BOB_ROOT}/bin/bob-namespace-sandbox" -C && HAVE_SANDBOX=1 || true

HAVE_PWSH=0
{ type -p pwsh >/dev/null 2>&1 || type -p powershell >/dev/null 2>&1 ; } && HAVE_PWSH=1 || true

if is_win32 ; then
	SCRIPT=build.cmd
else
	SCRIPT=build.sh
fi

# Run the "exec" checks against an already built package. Verifies that a
# single command works, that several commands run one after another and that
# execution stops at the first command that fails. The commands themselves
# are language specific and passed in via $CMD_ONE, $CMD_TWO, $CMD_FAIL and
# $CMD_THREE.
run_exec_checks()
{
	local buildsh="dev/build/$1/1/$SCRIPT"
	local ws="dev/build/$1/1/workspace"

	rm -f "$ws"/*.txt

	# A single command is executed.
	"$buildsh" exec "$CMD_ONE"
	expect_exist "$ws/one.txt"

	# Several commands are executed one after another.
	rm -f "$ws"/*.txt
	"$buildsh" exec "$CMD_ONE" "$CMD_TWO"
	expect_exist "$ws/one.txt" "$ws/two.txt"

	# Execution stops at the first command that fails.
	rm -f "$ws"/*.txt
	expect_fail "$buildsh" exec "$CMD_ONE" "$CMD_FAIL" "$CMD_THREE"
	expect_exist "$ws/one.txt"
	expect_not_exist "$ws/three.txt"
}

#
# bash - the default script language. No interpreter dependency at all.
#
CMD_ONE='echo -n one > one.txt'
CMD_TWO='echo -n two > two.txt'
CMD_FAIL='exit 1'
CMD_THREE='echo -n three > three.txt'

cleanup
run_bob dev bash
run_exec_checks bash

if [[ $HAVE_SANDBOX == 1 ]] ; then
	cleanup
	run_bob dev bash --slim-sandbox
	run_exec_checks bash

	cleanup
	run_bob dev bash --strict-sandbox
	run_exec_checks bash
fi

#
# python - a "python3" binary must be available in $PATH. Bob would use its
# own, bundled interpreter outside of a sandbox, but we require "python3"
# unconditionally here to keep this test's requirements simple and uniform.
#
CMD_ONE="open('one.txt', 'w').write('one')"
CMD_TWO="open('two.txt', 'w').write('two')"
CMD_FAIL='exit(1)'
CMD_THREE="open('three.txt', 'w').write('three')"

cleanup
run_bob dev python
run_exec_checks python

if [[ $HAVE_SANDBOX == 1 ]] ; then
	cleanup
	run_bob dev python --slim-sandbox
	run_exec_checks python

	cleanup
	run_bob dev python --strict-sandbox
	run_exec_checks python
fi

#
# PowerShell - always needs a real "pwsh"/"powershell" interpreter, with or
# without a sandbox.
#
if [[ $HAVE_PWSH == 1 ]] ; then
	CMD_ONE='Set-Content -NoNewline -Path one.txt -Value "one"'
	CMD_TWO='Set-Content -NoNewline -Path two.txt -Value "two"'
	CMD_FAIL='exit 1'
	CMD_THREE='Set-Content -NoNewline -Path three.txt -Value "three"'

	cleanup
	run_bob dev pwsh
	run_exec_checks pwsh

	if [[ $HAVE_SANDBOX == 1 ]] ; then
		cleanup
		run_bob dev pwsh --slim-sandbox
		run_exec_checks pwsh

		cleanup
		run_bob dev pwsh --strict-sandbox
		run_exec_checks pwsh
	fi
fi
