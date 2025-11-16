#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."

# Checks that variant-ids and the internal step structure is stable.

mkdir -p output
for i in checkouts env include sandbox tools ; do
	run_bob project -n --sandbox dumper root-$i output/$i.txt
	diff -uZ specs/$i.txt output/$i.txt
done
