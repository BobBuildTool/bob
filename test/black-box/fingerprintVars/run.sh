#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

# Uses hard coded build-ids. Will not work on Windows.
if [[ $(uname -o) == Msys ]] ; then
	skip
fi

# Test the old and new behaviour of the fingerprintVars policy.

cleanup
rm -rf output

countArtifacts()
{
	find output/ -type f | wc -l
}

runSpecific()
{
	rm -rf dev
	run_bob dev --upload "root-$1"
}

# Test new behaviour of fingerprintVars policy
# ============================================
#
# The fingerprintVars key should control which variables the fingerprintScript
# sees.

cleanup
rm -rf output

cat >config.yaml <<EOF
bobMinimumVersion: "0.24rc1"
policies:
    fingerprintVars: True
EOF

# All "unset" variants should produce exactly one artifact.
runSpecific allUnset1
runSpecific allUnset2
runSpecific allUnset3

if [[ $(countArtifacts) -ne 1 ]] ; then
	echo "Expected only one fingerprinted artifact" >&2
	exit 1
fi
expect_exist output/27/2c/b9188a1e58d3cf5a465e8f386d8b26f98e63e3007b3c106c2803013085f50ba688147b336b95-1.tgz

# The various subsets must create a different artifact each.
rm -rf output
runSpecific subSet1
runSpecific subSet2
runSpecific subSet3

if [[ $(countArtifacts) -ne 3 ]] ; then
	echo "Expected exactly three fingerprinted artifacts" >&2
	exit 1
fi
expect_exist output/27/2c/b9188a1e58d3cf5a465e8f386d8b26f98e63224b43f4071704cec3ba128ec08ea3ca90f75681-1.tgz
expect_exist output/27/2c/b9188a1e58d3cf5a465e8f386d8b26f98e635eeba3710a56fbfda09e408be6513fe61faff513-1.tgz
expect_exist output/27/2c/b9188a1e58d3cf5a465e8f386d8b26f98e639fbb8ed1de1672bf7b97ad217d28288097f5284a-1.tgz
