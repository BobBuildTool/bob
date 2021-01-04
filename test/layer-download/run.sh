#!/bin/bash -e
. ../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

cleanup
rm -rf default.yaml

# setup local archive
trap 'rm -rf "${archiveDir}"' EXIT
archiveDir=$(mktemp -d)
cat >default.yaml <<EOF
archive:
  -
    backend: file
    path: "$archiveDir"
EOF

function cleanArchive() {
    rm -rf "${archiveDir}"
    mkdir "${archiveDir}"
}

function checkBuildDirExist() {
    [ $1 -eq 0 ] && expect_not_exist "work/A/A/build" || expect_exist "work/A/A/build"
    [ $2 -eq 0 ] && expect_not_exist "work/B/B/build" || expect_exist "work/B/B/build"
    [ $3 -eq 0 ] && expect_not_exist "work/C/C/build" || expect_exist "work/C/C/build"
    [ $4 -eq 0 ] && expect_not_exist "work/D/D/build" || expect_exist "work/D/D/build"
    [ $5 -eq 0 ] && expect_not_exist "work/A/A2/build" || expect_exist "work/A/A2/build"
}

function checkDistDirExist() {
    [ $1 -eq 0 ] && expect_not_exist "work/A/A/dist" || expect_exist "work/A/A/dist"
    [ $2 -eq 0 ] && expect_not_exist "work/B/B/dist" || expect_exist "work/B/B/dist"
    [ $3 -eq 0 ] && expect_not_exist "work/C/C/dist" || expect_exist "work/C/C/dist"
    [ $4 -eq 0 ] && expect_not_exist "work/D/D/dist" || expect_exist "work/D/D/dist"
    [ $5 -eq 0 ] && expect_not_exist "work/A/A2/dist" || expect_exist "work/A/A2/dist"
}

# build and upload all
run_bob build --download no --upload root

# no download layer
cleanup
run_bob build --download deps root
checkBuildDirExist 0 0 0 0 0
checkDistDirExist 1 1 1 0 0

# download deps
cleanup
run_bob build --download deps --download-layer no=componentA root
checkBuildDirExist 1 0 0 0 0
checkDistDirExist 1 1 1 1 0

cleanup
run_bob build --download deps --download-layer no=componentA --download-layer no=componentB root
checkBuildDirExist 1 1 0 0 0
checkDistDirExist 1 1 1 1 0

cleanup
run_bob build --download deps --download-layer no="component.*" root
checkBuildDirExist 1 1 0 0 0
checkDistDirExist 1 1 1 1 0

# download yes
cleanup
run_bob build --download yes root/A::A
checkBuildDirExist 0 0 0 0 0
checkDistDirExist 1 0 0 0 0

cleanup
run_bob build --download yes --download-layer no=componentA root/A::A
checkBuildDirExist 1 0 0 0 0
checkDistDirExist 1 0 0 1 0

cleanup
run_bob build --download yes --download-layer no="component.*" root/A::A
checkBuildDirExist 1 0 0 0 0
checkDistDirExist 1 0 0 1 0

# download no
cleanup
run_bob build --download no root
checkBuildDirExist 1 1 1 1 1
checkDistDirExist 1 1 1 1 1

cleanup
run_bob build --download no --download-layer yes=componentB root
checkBuildDirExist 1 0 1 1 1
checkDistDirExist 1 1 1 1 1

# download forced and forced-deps
cleanArchive
cleanup
run_bob build --download no root/B::B
run_bob build --upload root

cleanup
expect_fail run_bob build --download forced root/B::B
run_bob build --download forced --download-layer no=componentB root/B::B

cleanup
expect_fail run_bob build --download forced-deps root
run_bob build --download forced-deps --download-layer no=componentB root

