#!/bin/bash -e
. ../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

# Checks that variant-ids and the internal step structure is stable.

mkdir -p output
for i in checkouts env include sandbox tools ; do
	run_bob project -n --sandbox dumper root-$i output/$i.txt
	diff -u specs/$i.txt output/$i.txt
done
