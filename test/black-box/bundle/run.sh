#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

cleanup
rm -rf default.yaml

# trap 'rm -rf "${archiveDir}" "${srcDir}" "${srcDirTmp}" default.yaml bundle.zip' EXIT
archiveDir=$(mktemp -d)
srcDir=$(mktemp -d)
srcDirTemp=$(mktemp -d)

rm -rf $srcDir/*
# setup sources for checkouts
pushd $srcDir
mkdir -p git_scm
pushd git_scm
git init -b master .
git config user.email "bob@bob.bob"
git config user.name test
echo "Hello World!" > hello.txt
git add hello.txt
git commit -m "hello"
echo "foo" > foo.txt
git add foo.txt
git commit -m "foo"

GIT_URL=$(pwd)
GIT_COMMIT=$(git rev-parse HEAD)
popd #git_scm

mkdir -p tar
pushd tar
dd if=/dev/zero of=test.dat bs=1K count=1
tar cvf test.tar test.dat
TAR_URL=$(pwd)/test.tar
TAR_SHA1=$(sha1sum test.tar | cut -d ' ' -f1)
popd #tar
popd # srcDir

function run_src_upload_tests () {
  # cleanup
  rm -rf work dev $archiveDir/*

  cat > default.yaml <<EOF
archive:
  -
    name: "local"
    backend: file
    path: "$(mangle_path "$archiveDir")"
    flags: [src-download, src-upload]
EOF
  run_bob dev root -DTAR_URL=${TAR_URL} -DTAR_SHA1=${TAR_SHA1} -DGIT_URL=${GIT_URL} -DGIT_COMMIT=${GIT_COMMIT} --upload

  rm dev -rf
  run_bob dev root -DTAR_URL=${TAR_URL} -DTAR_SHA1=${TAR_SHA1} -DGIT_URL=${GIT_URL} -DGIT_COMMIT=${GIT_COMMIT} --download yes
  expect_exist dev/src/git/1/workspace/hello.txt
  expect_not_exist dev/src/git/1/workspace/.git

  # test corrupted source archive leads to build error
  rm dev -rf
  pushd $archiveDir
  ARTIFACTS=( $(/usr/bin/find . -type f) )
  A=${ARTIFACTS[0]}
  pushd $(dirname $A)
  tar xvf $(basename $A)
  echo "test" > content/foo.dat
  tar --pax-option bob-archive-vsn=1 -zcf "$(basename $A)" meta content
  rm content meta -rf
  popd
  popd

  expect_fail run_bob dev root -DTAR_URL=${TAR_URL} -DTAR_SHA1=${TAR_SHA1} -DGIT_URL=${GIT_URL} -DGIT_COMMIT=${GIT_COMMIT} --download yes

  rm default.yaml
}

function _run_bob() {
  run_bob dev root -DTAR_URL=${TAR_URL} -DTAR_SHA1=${TAR_SHA1} \
                   -DGIT_URL=${GIT_URL} -DGIT_COMMIT=${GIT_COMMIT} \
                   -v \
                   "$@"
}

function _run_bundle () {
  _run_bob --bundle bundle.zip "$@"
}

function _run_unbundle () {
  cleanup
  run_bob dev root -DTAR_SHA1=${TAR_SHA1} \
                   -DGIT_COMMIT=${GIT_COMMIT} \
                   --unbundle bundle.zip "$@"
}

function run_bundle_tests () {
  cleanup
  _run_bundle
  _run_unbundle -DTAR_URL="/nonexisting/test.tar" -DGIT_URL="/nonexisting/test.git"
  expect_not_exist dev/src/git/1/workspace/.git

  # editing code + build should work as usual
  echo "hello" > dev/src/git/1/workspace/hello.txt
  _run_bob -b
  expect_exist dev/dist/git/1/workspace/hello.txt

  rm dev/dist/git/1/workspace/hello.txt
  _run_unbundle -DTAR_URL="/nonexisting/test.tar" -DGIT_URL="/nonexisting/test.git"
  expect_exist dev/dist/git/1/workspace/hello.txt

  # switching from bundle mode to normal mode should move to attic
  touch dev/src/git/1/workspace/canary.txt
  _run_bob
  expect_not_exist dev/src/git/1/workspace/canary.txt

  # switching from normal mode to bundle mode should move to attic
  touch dev/src/git/1/workspace/canary.txt
  _run_unbundle -DTAR_URL="/nonexisting/test.tar" -DGIT_URL="/nonexisting/test.git"
  expect_not_exist dev/src/git/1/workspace/canary.txt

  # we always bundle clean sources
  cleanup
  _run_bob
  echo "hello" > dev/src/git/1/workspace/new.txt
  # XXX: clean checkout does not clean url scms :/
  # echo "hello" > dev/src/tar/1/workspace/new.txt
  _run_bundle
  expect_not_exist dev/src/git/1/workspace/new.txt
  # expect_not_exist dev/src/tar/1/workspace/new.txt
  _run_unbundle -DTAR_URL="/nonexisting/test.tar" -DGIT_URL="/nonexisting/test.git"

  # test bundle-vcs option
  cleanup bundle.zip
  _run_bundle --bundle-vcs
  _run_unbundle -DTAR_URL="/nonexisting/test.tar" -DGIT_URL="/nonexisting/test.git"
  expect_exist dev/src/git/1/workspace/.git

  # test exclude
  cleanup bundle.zip
  _run_bundle --bundle-exclude "ta*"
  expect_fail _run_unbundle -DTAR_URL="/nonexisting/test.tar" -DGIT_URL="/nonexisting/test.git"
  expect_not_exist dev/src/tar/1/workspace/test.dat
}

run_src_upload_tests
run_bundle_tests
