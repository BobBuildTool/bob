# This file holds common test functions and is sourced by run-tests.sh or the
# black box tests directly. It is not intended to be executed directly.

cd "$(dirname "$0")"

# Check if already sourced
if [[ "$(type -t run_bob)" == function ]] ; then
	return
fi

PS4='+${BASH_SOURCE##*/}:${LINENO} '

BOB_ROOT="$PWD/$1"
if [[ ! -d "$BOB_ROOT/pym/bob" ]] ; then
	echo "From where are you calling me?" >&2
	exit 1
fi

export PYTHONPATH="${BOB_ROOT}/pym"

# Determine test environment if not already provided by run-tests.sh...
if [[ -n $TEST_ENVIRONMENT ]] ; then
	:
elif [[ $(uname -o) == Msys ]] ; then
	case "$(python -c "import sys; print(sys.platform)")" in
		win32)
			TEST_ENVIRONMENT=win32
			;;
		msys | cygwin)
			TEST_ENVIRONMENT=msys
			;;
		*)
			echo "Unknown MSYS environment!"
			exit 1
			;;
	esac
else
	TEST_ENVIRONMENT=posix
fi

set -o errtrace
trap 'err=$?; echo "ERROR: Command ${BASH_COMMAND} failed with code $err" >&2; exit $err' ERR

is_win32()
{
	if [[ $TEST_ENVIRONMENT == win32 ]] ; then
		return 0
	else
		return 1
	fi
}

is_msys()
{
	if [[ $TEST_ENVIRONMENT == msys ]] ; then
		return 0
	else
		return 1
	fi
}

is_posix()
{
	if [[ $TEST_ENVIRONMENT == posix ]] ; then
		return 0
	else
		return 1
	fi
}

mangle_path()
{
	if [[ $TEST_ENVIRONMENT == win32 ]] ; then
		cygpath -m "$1"
	else
		echo "$1"
	fi
}

native_path()
{
	if [[ $TEST_ENVIRONMENT == win32 ]] ; then
		cygpath -w "$1"
	else
		echo "$1"
	fi
}

file_url()
{
	if is_win32 ; then
		echo "file:///$(cygpath -m "$1")"
	else
		echo "file://$1"
	fi
}

cleanup()
{
	rm -rf work dev .bob-* "$@"
}

# Run bob in testing environment. Adds the "package calculation check" (pkgck)
# and "no global defaults" debug switches.
run_bob()
{
	if is_win32 ; then
		python "$BOB_ROOT/bob" --debug=pkgck,ngd,audit "$@"
	else
		"$BOB_ROOT/bob" --debug=pkgck,ngd,audit "$@"
	fi
}

# Run bob on pty. This only works on Linux, though.
run_bob_tty()
{
	script -c "/bin/bash -c \"$BOB_ROOT/bob --debug=pkgck,ngd,audit $*\"" -e /dev/null
}

# Run bob only with the "no global defaults" debug switch. Used for black box
# regression tests of the package calculation algorithm.
run_bob_plain()
{
	if is_win32 ; then
		python "$BOB_ROOT/bob" --debug=ngd "$@"
	else
		"$BOB_ROOT/bob" --debug=ngd "$@"
	fi
}

exec_blackbox_test()
{
	cleanup

	run_bob dev root
	RES=$(run_bob query-path -f '{dist}' --develop root)
	diff -NurpZ $RES output

	run_bob build root
	RES=$(run_bob query-path -f '{dist}' --release root)
	diff -NurpZ $RES output

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
	local i
	for i in "$@" ; do
		if [[ ! -e "$i" ]] ; then
			echo "Missing expected file: $i" >&2
			return 1
		fi
	done

	return 0
}

expect_not_exist()
{
	local i
	for i in "$@" ; do
		if [[ -e "$i" ]] ; then
			echo "Unexpected file: $i" >&2
			return 1
		fi
	done

	return 0
}

skip()
{
	exit 240
}

die()
{
	echo "$@"
	exit 1
}

expect_equal()
{
	if [[ "$1" != "$2" ]] ; then
		die "Failed: '$1' == '$2'"
	fi
}

expect_not_equal()
{
	if [[ "$1" = "$2" ]] ; then
		die "Failed: '$1' != '$2'"
	fi
}
