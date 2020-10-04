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

set -o errtrace
trap 'err=$?; echo "ERROR: Command ${BASH_COMMAND} failed with code $err" >&2; exit $err' ERR

cleanup()
{
	rm -rf work dev .bob-*
}

# Run bob in testing environment. Adds the "package calculation check" (pkgck)
# and "no global defaults" debug switches.
run_bob()
{
	${RUN_PYTHON3} "$BOB_ROOT/bob" --debug=pkgck,ngd "$@"
}

# Run bob only with the "no global defaults" debug switch. Used for black box
# regression tests of the package calculation algorithm.
run_bob_plain()
{
	${RUN_PYTHON3} "$BOB_ROOT/bob" --debug=ngd "$@"
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

expect_exist()
{
	if [[ ! -e "$1" ]] ; then
		echo "Missing expected file: $1" >&2
		return 1
	fi

	return 0
}

expect_not_exist()
{
	if [[ -e "$1" ]] ; then
		echo "Unexpected file: $1" >&2
		return 1
	fi

	return 0
}

skip()
{
	exit 240
}
