#!/bin/bash

cd "${0%/*}/.."
. ./test/test-lib.sh

USE_COVERAGE=0
FAILED=0
RUN_TEST_DIRS=( )

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

# run unit tests
echo "Run unit tests..."
pushd test > /dev/null
if ! $RUN -m unittest discover . ; then
	: $((FAILED++))
	echo "Some unit test(s) failed"
fi
popd > /dev/null

# run blackbox tests
echo "Run black box tests..."
for i in test/* ; do
	if [[ -d $i && -e $i/run.sh ]] ; then
		RUN_TEST_DIRS+=( $i )

		echo "   " $i
		(
			set -o pipefail
			set -e
			cd $i
			. run.sh > log.txt
		)

		if [[ $? -ne 0 ]] ; then
			: $((FAILED++))
			echo $i failed
		fi
	fi
done

# collect coverage
if [[ $USE_COVERAGE -eq 1 ]]; then
	coverage3 combine test "${RUN_TEST_DIRS[@]}"
fi

exit $FAILED
