#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

cleanup
rm -f test_bundle.tar

trap 'rm -f test_bundle.tar' EXIT

# generate some input
if [[ ! -d _input ]]; then
  mkdir -p _input
  pushd _input
  mkdir -p a b c d
  
  for x in a b c d; do
    dd if=/dev/urandom of=$x/test.dat bs=1M count=10
    tar czf $x/test.tgz $x/test.dat
  done

  popd # _input
fi

run_bob dev bundle -j 5 \
	-DINPUT_BASE="$PWD/_input" \
	-DINPUT_A_DIGEST="$(sha1sum _input/a/test.tgz | cut -d ' ' -f 1)" \
	-DINPUT_B_DIGEST="$(sha1sum _input/b/test.tgz | cut -d ' ' -f 1)" \
	-DINPUT_C_DIGEST="$(sha1sum _input/c/test.tgz | cut -d ' ' -f 1)" \
	-DINPUT_D_DIGEST="$(sha1sum _input/d/test.tgz | cut -d ' ' -f 1)"
expect_exist dev/src/input/1/download

run_bob dev bundle -j 5 \
	-DINPUT_BASE="$PWD/_input" \
	-DINPUT_A_DIGEST="$(sha1sum _input/a/test.tgz | cut -d ' ' -f 1)" \
	-DINPUT_B_DIGEST="$(sha1sum _input/b/test.tgz | cut -d ' ' -f 1)" \
	-DINPUT_C_DIGEST="$(sha1sum _input/c/test.tgz | cut -d ' ' -f 1)" \
	-DINPUT_D_DIGEST="$(sha1sum _input/d/test.tgz | cut -d ' ' -f 1)" \
	--bundle test_bundle.tar
expect_exist test_bundle.tar

cleanup
rm -f test_bundle.tar

run_bob dev bundle -j 5 \
	-DINPUT_BASE="$PWD/_input" \
	-DINPUT_A_DIGEST="$(sha1sum _input/a/test.tgz | cut -d ' ' -f 1)" \
	-DINPUT_B_DIGEST="$(sha1sum _input/b/test.tgz | cut -d ' ' -f 1)" \
	-DINPUT_C_DIGEST="$(sha1sum _input/c/test.tgz | cut -d ' ' -f 1)" \
	-DINPUT_D_DIGEST="$(sha1sum _input/d/test.tgz | cut -d ' ' -f 1)" \
	--bundle test_bundle.tar
expect_exist test_bundle.tar

cleanup
run_bob dev bundle -j 5 \
	-DINPUT_BASE="/notexisting" \
	-DINPUT_A_DIGEST="$(sha1sum _input/a/test.tgz | cut -d ' ' -f 1)" \
	-DINPUT_B_DIGEST="$(sha1sum _input/b/test.tgz | cut -d ' ' -f 1)" \
	-DINPUT_C_DIGEST="$(sha1sum _input/c/test.tgz | cut -d ' ' -f 1)" \
	-DINPUT_D_DIGEST="$(sha1sum _input/d/test.tgz | cut -d ' ' -f 1)" \
	--bundle test_bundle.tar --unbundle
expect_not_exist dev/src/input/1/download/

cleanup
rm -f test_bundle.tar

expect_fail run_bob dev bundle -j 5 \
	-DINPUT_BASE="$PWD/_input" \
	-DINPUT_A_DIGEST="$(sha1sum _input/a/test.tgz | cut -d ' ' -f 1)" \
	-DINPUT_B_DIGEST="$(sha1sum _input/b/test.tgz | cut -d ' ' -f 1)" \
	-DINPUT_C_DIGEST="$(sha1sum _input/c/test.tgz | cut -d ' ' -f 1)" \
	-DINPUT_D_DIGEST="$(sha1sum _input/d/test.tgz | cut -d ' ' -f 1)" \
	-DTEST_INDETERMINISTIC_INPUT=True \
	--bundle test_bundle.tar
