#!/bin/bash

set -o pipefail

usage()
{
	cat <<EOF
usage: ${0##*/} [-h] [-u PATTERN] [-b PATTERN] [-c]

optional arguments:
  -h              show this help message and exit
  -u PATTERN      Only execute unit tests matching PATTERN
  -b PATTERN      Only execute black box tests matching PATTERN
  -c              Create HTML coverage report
EOF
}

# move to root directory
cd "${0%/*}/.."
. ./test/test-lib.sh

COVERAGE=
FAILED=0
RUN_TEST_DIRS=( )
GEN_HTML=0
unset RUN_UNITTEST_PAT
unset RUN_BLACKBOX_PAT

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
    else
        RUN_PYTHON3=python3
        COVERAGE=
        echo "coverage3 is installed but not in the current environment" >&2
    fi
else
	RUN_PYTHON3=python3
fi

# option processing
while getopts ":hcb:u:" opt; do
	case $opt in
		h)
			usage
			exit 0
			;;
		c)
			GEN_HTML=1
			;;
		b)
			RUN_BLACKBOX_PAT="$OPTARG"
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
	for i in test_*.py ; do
		if [[ "${i%%.py}" == $RUN_UNITTEST_PAT ]] ; then
			echo -n "   ${i%%.py} ... "
			{
				echo "======================================================================"
				echo "Test: $i"
			} >> log.txt
			if $RUN -m unittest -v $i 2>&1 | tee log-cmd.txt >> log.txt ; then
				echo "ok"
			else
				echo "FAIL (log follows...)"
				: $((FAILED++))
				cat -n log-cmd.txt
			fi
		fi
	done
fi

# run blackbox tests
if [[ -n "$RUN_BLACKBOX_PAT" ]] ; then
	echo "Run black box tests..."
	for i in * ; do
		if [[ -d $i && -e "$i/run.sh" && "$i" == $RUN_BLACKBOX_PAT ]] ; then
			RUN_TEST_DIRS+=( "test/$i" )

			echo -n "   $i ... "
			{
				echo "======================================================================"
				echo "Test: $i"
			} >> log.txt
			(
				set -o pipefail
				set -ex
				cd "$i"
				. run.sh 2>&1 | tee log.txt
			) 2>&1 | tee log-cmd.txt >> log.txt

			ret=$?
			if [[ $ret -eq 240 ]] ; then
				echo "skipped"
			elif [[ $ret -ne 0 ]] ; then
				echo "FAIL (exit $ret, log follows...)"
				: $((FAILED++))
				cat -n log-cmd.txt
			else
				echo "ok"
			fi
		fi
	done
fi

popd > /dev/null

# collect coverage
if [[ -n $COVERAGE ]]; then
	$COVERAGE combine test "${RUN_TEST_DIRS[@]}"
	if [[ $GEN_HTML -eq 1 ]] ; then
		$COVERAGE html
	fi
fi

exit $FAILED
