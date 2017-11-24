# This file holds common test functions and is sourced by run-tests.sh or the
# black box tests directly. It is not intended to be executed directly.

# Check if already sourced
if [[ "$(type -t run_bob)" == function ]] ; then
	return
fi

if [[ -d "pym/bob" ]] ; then
	BOB_ROOT="${PWD}"
elif [[ -d "../../pym/bob" ]] ; then
	BOB_ROOT="${PWD}/../.."
else
	echo "From where are you calling me?" >&2
	exit 1
fi

export PYTHONPATH="${BOB_ROOT}/pym"

cleanup()
{
	rm -rf work dev .bob-*
}

# Run bob in testing environment. Adds the "package calculation check" (pkgck)
# and "no global defaults" debug switches.
run_bob()
{
	${RUN:-python3} -m bob.scripts bob "$BOB_ROOT" --debug=pkgck,ngd "$@"
}

# Run bob only with the "no global defaults" debug switch. Used for black box
# regression tests of the package calculation algorithm.
run_bob_plain()
{
	${RUN:-python3} -m bob.scripts bob "$BOB_ROOT" --debug=ngd "$@"
}

exec_blackbox_test()
{
	cleanup

	run_bob dev root
	RES=$(run_bob query-path -f '{dist}' --develop root)
	diff -Nurp $RES output

	run_bob build root
	RES=$(run_bob query-path -f '{dist}' --release root)
	diff -Nurp $RES output

	run_bob clean
}

expect_fail()
{
	"$@" 2>&1 || if [[ $? -ne 1 ]] ; then
		echo "Unexpected return code: $*" >&2
		return 1
	else
		return 0
	fi
	echo "Expected command to fail: $*" >&2
	return 1
}

expect_output()
{
	local EXP="$1"
	local RES="$( "${@:2}" )"
	if [[ "$RES" != "$EXP" ]] ; then
		echo "Unexpected output: '$RES'" >&2
		echo "Expected: '$EXP'" >&2
		echo "Command: ${@:2}" >&2
		return 1
	fi

	return 0
}
