#!/bin/bash -ex
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

DIRS="recipes1 recipes2"

cleanAll() {
for i in $DIRS; do
    pushd $i;
    cleanup;
    popd;
done
}
cleanAll

trap 'rm -rf "${tmpDir}"' EXIT
tmpDir=$(mktemp -d)
uploadDir="$tmpDir/upload"
cfg="$tmpDir/cfg"

writeCfg()
{
cat >"${cfg}.yaml" <<EOF
archive:
   -
    backend: file
    path: $uploadDir
EOF
}

writeCfg

# we emulate a non-relocatable packages
# by writing the workspace path in to path.txt

checkPath() {
    PKG=$1
    WORKSPACE=$(run_bob query-path -f '{dist}' --release $PKG)
    DATA=$(cat $WORKSPACE/path.txt)
    if [[ "$PWD/$WORKSPACE" != "$DATA" ]]; then
        echo "failure building non-relocated: path does not match"
        exit 1
    fi
}

for i in $DIRS; do
pushd $i;
run_bob build -c $cfg --upload root;
checkPath root;
popd;
done

