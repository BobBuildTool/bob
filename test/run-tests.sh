#!/bin/bash

cd "${0%/*}/.."
export PYTHONPATH="${PWD}/pym"
BOB_ROOT="$PWD"

USE_COVERAGE=0
if type -fp coverage3 >/dev/null; then
    # make sure coverage is installed in the current environment
    if python3 -c "import coverage" 2>/dev/null; then
	    RUN="coverage3 run --source $PWD/pym  --parallel-mode"
        USE_COVERAGE=1
    else
        RUN=python3
        echo "coverage3 is installed but not in the current environment"
    fi
else
	RUN=python3
fi

FAILED=0

echo "Run unit tests..."
pushd test > /dev/null
if ! $RUN -m unittest discover . ; then
	: $((FAILED++))
	echo "Some unit test(s) failed"
fi
popd > /dev/null

run_bob()
{
	$RUN -m bob.scripts bob "$BOB_ROOT" "$@"
}

exec_blackbox_test()
{
	run_bob dev root > log.txt
	RES=$(sed -ne '/^Build result is in/s/.* //p' log.txt)
	diff -Nurp $RES output

	run_bob build root > log.txt
	RES=$(sed -ne '/^Build result is in/s/.* //p' log.txt)
	diff -Nurp $RES output

	run_bob clean
}

declare -a RUN_TEST_DIRS

run_test()
{
	RUN_TEST_DIRS+=( $1 )

	echo "   " $1
	(
		set -o pipefail
		set -e
		cd $1
		rm -rf work dev .bob-*
		. run.sh > log.txt
	)

	if [[ $? -ne 0 ]] ; then
		: $((FAILED++))
		echo $1 failed
	fi
}

# run blackbox tests
echo "Run black box tests..."
for i in test/* ; do
	if [[ -d $i && -e $i/run.sh ]] ; then
		run_test $i
	fi
done

# collect coverage
if [[ $USE_COVERAGE -eq 1 ]]; then
	coverage3 combine test "${RUN_TEST_DIRS[@]}"
fi

exit $FAILED
