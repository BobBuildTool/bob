#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."

cleanup
rm -rf default.yaml

# setup local archive
trap 'rm -rf "${archiveDir}" "${archiveDir2}"' EXIT
archiveDir=$(mktemp -d)
archiveDir2=$(mktemp -d)


function create_config () {
  cat > default.yaml <<EOF
archive:
  -
    name: "local"
    backend: file
    path: "$(mangle_path "$archiveDir")"
    flags: [download, upload, managed]
EOF
  if [[ "$1" == "multi" ]]; then
    cat >> default.yaml << EOF
  -
    name: "local2"
    backend: file
    path: "$(mangle_path "$archiveDir2")"
    flags: [download, upload, managed]
EOF
  fi
}

function clean_output () {
  echo $(echo "$@" | sed '/^archive/d' | sed "s/^[ \t]*//")
}

function run_single_tests () {
  # cleanup
  rm -rf work $archiveDir/*
  if [[ "$1" == "local" ]]; then
    LOCAL=true
    BOB_ARGS="-l"
  fi
  # fill archive
  run_bob build --download=no --upload root-alpha root-bravo
  FINGERPRINT=Alice run_bob build --force --download=no --upload root-alpha root-bravo

  if [[ $LOCAL ]]; then pushd $archiveDir; fi
  run_bob archive ${BOB_ARGS} scan

  # first try to keep everything by using multiple expressions
  oldNum=$(/usr/bin/find -name '*.tgz' | wc -l)
  run_bob archive ${BOB_ARGS} clean -v 'metaEnv.TYPE == "alpha"' 'meta.package == "root-bravo"'
  newNum=$(/usr/bin/find -name '*.tgz' | wc -l)
  test $oldNum -eq $newNum

  # Find one of the artifacts
  found=$(clean_output "$(run_bob archive ${BOB_ARGS} find 'meta.package == "root-bravo"')")
  if [[ ! $LOCAL ]]; then pushd $archiveDir; fi
  expect_exist "$found"
  if [[ ! $LOCAL ]]; then popd; fi

  # selectively keep one half
  run_bob archive ${BOB_ARGS} clean --dry-run 'metaEnv.TYPE == "alpha"'
  run_bob archive ${BOB_ARGS} clean -v 'metaEnv.TYPE == "alpha"'
  if [[ $LOCAL ]]; then popd; fi

  # alpha can be downloaded, bravo must fail
  rm -rf work
  run_bob build --download=forced root-alpha
  expect_fail run_bob build --download=forced root-bravo

  # Update archive and rescan. Add some invalid files too.
  rm -rf work $archiveDir/*
  run_bob build --force --download=no --upload root-alpha root-bravo
  pushd $archiveDir
  mkdir -p 64/ad
  /usr/bin/tar zcf 64/ad/6386bae45ebd6788e404758a247e26e5c778-1.tgz /dev/zero
  touch 64/ad/aabbcc-too-short.tgz
  if [[ $LOCAL ]]; then run_bob archive ${BOB_ARGS} scan; fi
  popd
  if [[ ! $LOCAL ]]; then run_bob archive ${BOB_ARGS} scan; fi

  # Test that -v doesn't catch fire
  run_bob archive ${BOB_ARGS} scan -v
  run_bob archive ${BOB_ARGS} clean -v 'metaEnv.TYPE == "alpha"'

  # Test for --fail option (--fail only reports -1 if there are no files matching the pattern xx/xx/hash-1.tgz)
  if [[ ! $LOCAL ]]; then
    run_bob archive ${BOB_ARGS} scan --fail -v
    run_bob archive ${BOB_ARGS} clean --fail -v 'metaEnv.TYPE == "alpha"'
    rm -rf work $archiveDir/*
    expect_fail run_bob archive ${BOB_ARGS} scan --fail -v
    expect_fail run_bob archive ${BOB_ARGS} clean --fail -v 'metaEnv.TYPE == "alpha"'
  else
    expect_fail run_bob archive ${BOB_ARGS} scan --fail -v
    expect_fail run_bob archive ${BOB_ARGS} clean --fail -v 'metaEnv.TYPE == "alpha"'
    pushd $archiveDir
    run_bob archive ${BOB_ARGS} scan --fail -v
    run_bob archive ${BOB_ARGS} clean --fail -v 'metaEnv.TYPE == "alpha"'
    popd
  fi

  # Test the "LIMIT" feature. Build a number or artifacts and keep only a portion
  # of them. By default the artifacts are sorted by build date and the most
  # recent is kept. Verify that the correct subset was retained.
  rm -rf "$archiveDir/"*
  run_bob build --download no --upload -q 'many-*'
  if [[ $LOCAL ]]; then pushd $archiveDir; fi
  run_bob archive ${BOB_ARGS} clean --fail -v 'meta.recipe == "many" LIMIT 3'
  if [[ $LOCAL ]]; then popd; fi
  test $(/usr/bin/find $archiveDir -name '*.tgz' | wc -l) -eq 3
  run_bob build --download forced --force many-07 many-06 many-05

  # Do the same again with ascending sorting and a different ordering key.  The
  # tricky part is that metaEnv.FUZZ is not set in all packages and such packages
  # must not be counted.
  rm -rf "$archiveDir/"*
  run_bob build --download no --force --upload -q 'many-*'
  if [[ $LOCAL ]]; then pushd $archiveDir; fi
  run_bob archive ${BOB_ARGS} clean --fail -v 'meta.recipe == "many" LIMIT 2 OrDeR By metaEnv.FUZZ ASC'
  if [[ $LOCAL ]]; then popd; fi
  test $(/usr/bin/find $archiveDir -name '*.tgz' | wc -l) -eq 2
  run_bob build --download forced --force many-01 many-03

  # Must fail if LIMIT is zero, invalid or negative
  if [[ $LOCAL ]]; then pushd $archiveDir; fi
  expect_fail run_bob archive ${BOB_ARGS} clean 'meta.recipe == "many" LIMIT 0'
  expect_fail run_bob archive ${BOB_ARGS} clean 'meta.recipe == "many" LIMIT -3'
  expect_fail run_bob archive ${BOB_ARGS} clean 'meta.recipe == "many" LIMIT foobar'
  if [[ $LOCAL ]]; then popd; fi

  # Build artifacts with special audit meta keys. Try to find them later.
  rm -rf "$archiveDir/"* work
  run_bob build --upload -M my-key=one root-alpha
  run_bob build --upload -M my-key=two root-bravo
  if [[ $LOCAL ]]; then pushd $archiveDir; fi
    run_bob archive ${BOB_ARGS} scan --fail
    found1=$(clean_output "$(run_bob archive ${BOB_ARGS} find -n 'meta.recipe == "root" && meta.my-key == "one"')")
    found2=$(clean_output "$(run_bob archive ${BOB_ARGS} find -n 'meta.recipe == "root" && meta.my-key == "two"')")
  if [[ ! $LOCAL ]]; then pushd $archiveDir; fi
  expect_exist "$found1"
  expect_exist "$found2"
  popd
  test "$found1" != "$found2"

  # Make sure invalid audit meta keys are rejected
  expect_fail run_bob build -M "!nv@l1d=key" root-alpha
}

function run_multi_tests () {
  # cleanup
  rm -rf work $archiveDir/*  $archiveDir2/*

  # fill archive
  run_bob build --download=no --upload root-alpha root-bravo
  FINGERPRINT=Alice run_bob build --force --download=no --upload root-alpha root-bravo

  # scan should fail as we did not specify one of the archives
  expect_fail run_bob archive scan
  # provide -a to scan all archives
  run_bob archive -a scan
  # clean up archive databses to allow proper rescanning
  rm .bob-archive*
  # scan single archive "local"
  run_bob archive -b "local" scan
  rm .bob-archive*
  # scan single archive "local2"
  run_bob archive -b "local2" scan
  rm .bob-archive*

  # test find
  run_bob archive -a scan
  # find also fails without specifying an archive
  expect_fail run_bob archive find 'meta.package == "root-bravo"'
  # check if file is found in archive 1 and archive 2
  found1=$(clean_output "$(run_bob archive -b "local" find 'meta.package == "root-bravo"')")
  found2=$(clean_output "$(run_bob archive -b "local2" find 'meta.package == "root-bravo"')")
  pushd $archiveDir
  expect_exist "$found1"
  pushd $archiveDir2
  expect_exist "$found2"
  popd
  popd
  # should find the same artifact twice
  found=$(clean_output "$(run_bob archive -a find 'meta.package == "root-bravo"')")
  foundarr=($found)
  test "${foundarr[0]}" == "${foundarr[1]}"
  pushd $archiveDir
  expect_exist "${foundarr[0]}"
  pushd $archiveDir2
  expect_exist "${foundarr[0]}"
  popd
  popd

  # test clean
  # artifact should only be removed in archive 1
  run_bob archive -b "local" clean 'meta.package != "root-bravo"'
  pushd $archiveDir
  expect_not_exist "${foundarr[0]}"
  popd
  pushd $archiveDir2
  expect_exist "${foundarr[0]}"
  popd
  # artifact should now be remove in archive 2, too
  run_bob archive -b "local2" clean 'meta.package != "root-bravo"'
  pushd $archiveDir
  expect_not_exist "${foundarr[0]}"
  popd
  pushd $archiveDir2
  expect_not_exist "${foundarr[0]}"
  popd
  # got to "reupload" them
  rm -rf work $archiveDir/*  $archiveDir2/*
  run_bob build --download=no --upload root-alpha root-bravo
  FINGERPRINT=Alice run_bob build --force --download=no --upload root-alpha root-bravo
  run_bob archive -a scan
  pushd $archiveDir
  expect_exist "${foundarr[0]}"
  popd
  pushd $archiveDir2
  expect_exist "${foundarr[0]}"
  popd
  # clean from all archives
  run_bob archive -a clean 'meta.package != "root-bravo"'
  pushd $archiveDir
  expect_not_exist "${foundarr[0]}"
  popd
  pushd $archiveDir2
  expect_not_exist "${foundarr[0]}"
  popd
}

create_config
# run tests with configuration with file backend
run_single_tests
# run tests with local directory
run_single_tests "local"
# tests with two archive backends
create_config "multi"
run_multi_tests
