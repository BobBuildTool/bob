#!/bin/bash

cd "${0%/*}/.."
export PYTHONPATH="${PWD}/pym"
BOB_ROOT="$PWD"

if [ -n "$(which coverage3)" ] ; then
	RUN="coverage3 run --source $PWD/pym  --parallel-mode"
else
	RUN=python3
fi

pushd test
$RUN -m unittest discover .
popd

FAILED=0

run_bob()
{
	$RUN -m bob.scrips bob "$BOB_ROOT" "$@"
}

run_blackbox_test()
{
	echo "Run blackbox test" $1
	(
		set -o pipefail
		set -e
		cd $1
		rm -rf work dev .bob-*

		run_bob dev root > log.txt
		RES=$(sed -ne '/^Build result is in/s/.* //p' log.txt)
		diff -Nurp $RES output

		run_bob build root > log.txt
		RES=$(sed -ne '/^Build result is in/s/.* //p' log.txt)
		diff -Nurp $RES output

		run_bob clean
	)

	if [[ $? -ne 0 ]] ; then
		: $((FAILED++))
		echo $1 failed
	fi
}

# run blackbox tests
for i in test/blackbox/* ; do
	run_blackbox_test $i
done

# collect coverage
if [ -n "$(which coverage3)" ] ; then
	coverage3 combine test test/blackbox/*
fi

exit $FAILED
