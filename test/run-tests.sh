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

USE_COVERAGE=0
FAILED=0
RUN_TEST_DIRS=( )
RUN_UNITTEST_PAT='*'
RUN_BLACKBOX_PAT='*'
GEN_HTML=0

# check if python coverage is installed
if type -fp coverage3 >/dev/null; then
    # make sure coverage is installed in the current environment
    if python3 -c "import coverage" 2>/dev/null; then
	    RUN="coverage3 run --source $PWD/pym  --parallel-mode"
        USE_COVERAGE=1
    else
        RUN=python3
        echo "coverage3 is installed but not in the current environment" >&2
    fi
else
	RUN=python3
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
			RUN_UNITTEST_PAT=''
			RUN_BLACKBOX_PAT="$OPTARG"
			;;
		u)
			RUN_BLACKBOX_PAT=''
			RUN_UNITTEST_PAT="$OPTARG"
			;;
		\?)
			echo "Invalid option: -$OPTARG" >&2
			exit 1
			;;
	esac
done

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
				set -e
				cd "$i"
				. run.sh 2>&1 | tee log.txt
			) | tee log-cmd.txt >> log.txt

			if [[ $? -ne 0 ]] ; then
				echo "FAIL (log follows...)"
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
if [[ $USE_COVERAGE -eq 1 ]]; then
	coverage3 combine test "${RUN_TEST_DIRS[@]}"
	if [[ $GEN_HTML -eq 1 ]] ; then
		coverage3 html
	fi
fi

exit $FAILED
