#!/bin/bash

set -o pipefail

usage()
{
	cat <<EOF
usage: ${0##*/} [-h] [-b PATTERN] [-c] [-j JOBS] [-n] [-u PATTERN]

optional arguments:
  -h              show this help message and exit
  -b PATTERN      Only execute black box tests matching PATTERN
  -c              Create HTML coverage report
  -j JOBS         Run JOBS tests in parallel (requires GNU parallel)
  -n              Do not record coverage even if python3-coverage is found.
  -u PATTERN      Only execute unit tests matching PATTERN
EOF
}

run_unit_test()
{
	local ret LOGFILE=$(mktemp)

	echo -n "   ${1%%.py} ... "
	{
		echo "======================================================================"
		echo "Test: $1"
	} > "$LOGFILE"
	if $RUN_PYTHON3 -m unittest -v $1 >>"$LOGFILE" 2>&1 ; then
		echo "ok"
		ret=0
	else
		echo "FAIL (log follows...)"
		ret=1
		cat -n "$LOGFILE"
	fi

	cat "$LOGFILE" >> log.txt
	rm "$LOGFILE"
	return $ret
}

run_blackbox_test()
{
	local ret LOGFILE=$(mktemp)

	echo -n "   $1 ... "
	{
		echo "======================================================================"
		echo "Test: $1"
	} > "$LOGFILE"
	(
		set -o pipefail
		set -ex
		cd "$1"
		. run.sh 2>&1 | tee log.txt
	) >>"$LOGFILE" 2>&1

	ret=$?
	if [[ $ret -eq 240 ]] ; then
		echo "skipped"
		ret=0
	elif [[ $ret -ne 0 ]] ; then
		echo "FAIL (exit $ret, log follows...)"
		cat -n "$LOGFILE"
	else
		echo "ok"
	fi

	cat "$LOGFILE" >> log.txt
	rm "$LOGFILE"
	return $ret
}

# Remove all GIT_ variables from environment. They will be set when running
# this script from "git rebase --exec" and blow up the git related tests.
unset "${!GIT_@}"

# move to root directory
cd "${0%/*}/.."
. ./test/test-lib.sh

COVERAGE=
FAILED=0
RUN_TEST_DIRS=( )
GEN_HTML=0
RUN_JOBS=
unset RUN_UNITTEST_PAT
unset RUN_BLACKBOX_PAT
export PYTHONDEVMODE=1
export PYTHONASYNCIODEBUG=1
export PYTHONWARNINGS=error
if [[ $(python3 --version) = "Python 3.9.7" ]] ; then
	# Stupid workaround for https://bugs.python.org/issue45097
	# Just ignore all deprecation warnings. To add insult to injury we
	# can't just ignore anything caused by asyncio because the full pacakge
	# name must be given in PYTHONWARNINGS.
	PYTHONWARNINGS+=",ignore::DeprecationWarning"
fi

# check if python coverage is installed
if type -fp coverage3 >/dev/null; then
   COVERAGE=coverage3
elif type -fp python3-coverage >/dev/null; then
   COVERAGE=python3-coverage
fi

if [[ -n $COVERAGE ]] ; then
    # make sure coverage is installed in the current environment
    if python3 -c "import coverage" 2>/dev/null; then
        RUN_PYTHON3="$COVERAGE run --source $PWD/pym  --parallel-mode"
	# The multiprocessing module is incompatible with coverage.py. Enable
	# the hack in pym/bob/utils.py to still get some data.
	export ENABLE_COVERAGE_HACK=1
    else
        RUN_PYTHON3=python3
        COVERAGE=
        echo "coverage3 is installed but not in the current environment" >&2
    fi
else
	RUN_PYTHON3=python3
fi

# option processing
while getopts ":hb:cj:nu:" opt; do
	case $opt in
		h)
			usage
			exit 0
			;;
		b)
			RUN_BLACKBOX_PAT="$OPTARG"
			;;
		c)
			GEN_HTML=1
			;;
		j)
			RUN_JOBS="$OPTARG"
			;;
		n)
			RUN_PYTHON3=python3
			COVERAGE=
			;;
		u)
			RUN_UNITTEST_PAT="$OPTARG"
			;;
		\?)
			echo "Invalid option: -$OPTARG" >&2
			exit 1
			;;
	esac
done

# execute everything if nothing was specified
if [[ -z ${RUN_UNITTEST_PAT+isset} && -z ${RUN_BLACKBOX_PAT+isset} ]] ; then
	RUN_BLACKBOX_PAT='*'
	RUN_UNITTEST_PAT='*'
else
	: "${RUN_BLACKBOX_PAT=}"
	: "${RUN_UNITTEST_PAT=}"
fi

# go to tests directory
pushd test > /dev/null

# remove stale coverage data
[[ -z $COVERAGE ]] || find -type f -name '.coverage.*' -delete || true

# add marker to log.txt
{
	echo "######################################################################"
	echo -n "Started: "
	date
	echo "Options: $*"
} >> log.txt

# run unit tests
if [[ -n "$RUN_UNITTEST_PAT" ]] ; then
	echo "Run unit tests..."
	RUN_TEST_NAMES=( )
	for i in test_*.py ; do
		if [[ "${i%%.py}" == $RUN_UNITTEST_PAT ]] ; then
			RUN_TEST_NAMES+=( "$i" )
		fi
	done

	if [[ ${#RUN_TEST_NAMES[@]} -eq 0 ]] ; then
		: # No tests matched
	elif type -p parallel >/dev/null && [[ ${RUN_JOBS:-} != 1 ]] ; then
		export -f run_unit_test
		export RUN_PYTHON3
		parallel ${RUN_JOBS:+-j $RUN_JOBS} run_unit_test ::: \
		  "${RUN_TEST_NAMES[@]}" || : $((FAILED+=$?))
	else
		for i in "${RUN_TEST_NAMES[@]}" ; do
			if ! run_unit_test "$i" ; then
				: $((FAILED++))
			fi
		done
	fi
fi

# run blackbox tests
if [[ -n "$RUN_BLACKBOX_PAT" ]] ; then
	echo "Run black box tests..."
	RUN_TEST_NAMES=( )
	for i in * ; do
		if [[ -d $i && -e "$i/run.sh" && "$i" == $RUN_BLACKBOX_PAT ]] ; then
			RUN_TEST_DIRS+=( "test/$i" )
			RUN_TEST_NAMES+=( "$i" )
		fi
	done

	if [[ ${#RUN_TEST_NAMES[@]} -eq 0 ]] ; then
		: # No tests matched
	elif type -p parallel >/dev/null && [[ ${RUN_JOBS:-} != 1 ]] ; then
		export -f run_blackbox_test
		export RUN_PYTHON3
		parallel ${RUN_JOBS:+-j $RUN_JOBS} run_blackbox_test ::: \
		  "${RUN_TEST_NAMES[@]}" || : $((FAILED+=$?))
	else
		for i in "${RUN_TEST_NAMES[@]}" ; do
			if ! run_blackbox_test "$i" ; then
				: $((FAILED++))
			fi
		done
	fi
fi

popd > /dev/null

# collect coverage
if [[ -n $COVERAGE ]]; then
	$COVERAGE combine $(find test/ -type f -name '.coverage.*' \
	                    -printf '%h\n' | sort -u)
	if [[ $GEN_HTML -eq 1 ]] ; then
		$COVERAGE html
	fi
fi

if [[ $FAILED -gt 127 ]] ; then
   FAILED=127
fi
exit $FAILED
