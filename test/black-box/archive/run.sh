#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

cleanup
rm -rf default.yaml

# setup local archive
trap 'rm -rf "${archiveDir}"' EXIT
archiveDir=$(mktemp -d)
cat >default.yaml <<EOF
archive:
  -
    name: "local"
    backend: file
    path: "$(mangle_path "$archiveDir")"
EOF

function clean_output () {
  echo $(echo "$@" | sed 1d | sed "s/^[ \t]*//")
}

function run_tests () {
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
# run tests with configuration with file backend
run_tests
# run tests with local directory
run_tests "local"
