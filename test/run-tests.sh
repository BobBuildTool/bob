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

pushd test
if ! $RUN -m unittest discover . ; then
	: $((FAILED++))
	echo "Some unit test(s) failed"
fi
popd

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

exec_generator_test()
{
	# just generate
	run_bob project -n g1 root > log.txt
	diff -u <(grep '^PLUGIN' log.txt) output-plugin.txt

	# run and generate
	run_bob project qt-creator root --kit 'dummy' > log.txt
	RES=$(sed -ne '/^Build result is in/s/.* //p' log.txt)
	diff -Nurp $RES output
	
   # run and generate
	run_bob project eclipseCdt root > log.txt
	RES=$(sed -ne '/^Build result is in/s/.* //p' log.txt)
	diff -Nurp $RES output

}

exec_fancy_test()
{
	. run.sh > log.txt
}

declare -a RUN_TEST_DIRS

run_test()
{
	RUN_TEST_DIRS+=( $1 )

	echo "Run" $1
	(
		set -o pipefail
		set -e
		cd $1
		rm -rf work dev .bob-*
		$2
	)

	if [[ $? -ne 0 ]] ; then
		: $((FAILED++))
		echo $1 failed
	fi
}

# run blackbox tests
for i in test/blackbox/* ; do
	run_test $i exec_blackbox_test
done

# run generator test
run_test test/generator exec_generator_test

# run query-path test
run_test test/query-path exec_fancy_test

run_test test/swap-deps exec_fancy_test
run_test test/checkout-only exec_fancy_test

# collect coverage
if [[ $USE_COVERAGE -eq 1 ]]; then
	coverage3 combine test "${RUN_TEST_DIRS[@]}"
fi

exit $FAILED
