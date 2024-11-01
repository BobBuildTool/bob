#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

tmp_dir=$(mktemp -d)
mkdir -p "$tmp_dir/"{foo,bar,baz,ext}
foo_dir="$tmp_dir/foo"
bar_dir="$tmp_dir/bar"
baz_dir="$tmp_dir/baz"
ext_dir="$tmp_dir/ext"
trap 'rm -rf "$tmp_dir" layers layers.attic log-status.txt' EXIT
cleanup

# build the git layer bar/1
pushd ${bar_dir}
mkdir recipes
cat > recipes/bar.yaml << EOF
packageScript: "/bin/true"
provideVars: 
  BAR_VERSION: "1"
EOF
git init .
git config user.email "bob@bob.bob"
git config user.name est

git add .
git commit -m "first commit"
bar_c0=$(git rev-parse HEAD)

sed -i 's/BAR_VERSION: "1"/BAR_VERSION: "3"/g' recipes/bar.yaml
git commit -a -m "bump bar"
bar_c1=$(git rev-parse HEAD)
popd # ${bar_dir}

pushd ${foo_dir}
cat > config.yaml << 'EOF'
layers:
  - name: bar
    scm: git
    url: "file://${BAR_DIR}"
    commit: "${BAR_2_COMMIT}"
  - name: baz
    scm: git
    url: "file://${BAZ_DIR}"
    commit: "${BAZ_COMMIT}"
  - name: baz1
    scm: git
    url: "file://${BAZ_DIR}"
    commit: "${BAZ1_COMMIT}"

EOF
mkdir recipes
cat > recipes/foo.yaml << EOF
buildScript: "true"
packageScript: "true"
EOF
git init .
git config user.email "bob@bob.bob"
git config user.name est

git add .
git commit -m "first commit"
foo_c0=$(git rev-parse HEAD)

cat > config.yaml << 'EOF'
layers:
  - name: bar
    scm: git
    url: "file://${BAR_DIR}"
    commit: "${BAR_2_COMMIT}"
EOF
git add .
git commit -m "remove baz"
foo_c1=$(git rev-parse HEAD)

git checkout -b branch_override
echo "override" > override
git add .
git commit -m "override"
popd # $foo_dir

pushd ${baz_dir}
mkdir recipes
cat > recipes/baz.yaml << EOF
buildScript: "true"
packageScript: "true"
provideVars:
   BAZ_VERSION: "1"
EOF
git init .
git config user.email "bob@bob.bob"
git config user.name est
git add .
git commit -m "first commit"
baz_c0=$(git rev-parse HEAD)
cat > recipes/baz.yaml << EOF
buildScript: "true"
packageScript: "true"
provideVars:
   BAZ_VERSION: "2"
EOF
git commit -a -m "bump"
baz_c1=$(git rev-parse HEAD)

mv recipes/baz.yaml recipes/baz1.yaml
git commit -a -m "rename"
baz_c2=$(git rev-parse HEAD)

popd # $baz_dir

# just build the root recipe. Layer should be fetched automatically.
run_bob dev root -DBAR_1_COMMIT=${bar_c0} -DBAR_2_COMMIT=${bar_c1} -DBAR_DIR=${bar_dir} \
	-DBAZ_DIR=${baz_dir} -DBAZ_COMMIT="${baz_c0}" \
	-DBAZ1_COMMIT="${baz_c2}" \
	-DFOO_DIR=${foo_dir} -DFOO_COMMIT="${foo_c0}" -vvv

# run update
run_bob layers update -DBAR_1_COMMIT=${bar_c0} -DBAR_2_COMMIT=${bar_c1} -DBAR_DIR=${bar_dir} \
	-DBAZ_DIR=${baz_dir} -DBAZ_COMMIT="${baz_c0}" \
	-DBAZ1_COMMIT="${baz_c2}" \
	-DFOO_DIR=${foo_dir} -DFOO_COMMIT="${foo_c0}"

# remove layers + clean
cleanup
rm -rf layers

# Do the build and update in an external build tree. SCM backed layers are
# checked out into the build tree rather than the project tree.
run_bob init . "$ext_dir"
pushd "$ext_dir"
run_bob dev root -DBAR_1_COMMIT=${bar_c0} -DBAR_2_COMMIT=${bar_c1} -DBAR_DIR=${bar_dir} \
	-DBAZ_DIR=${baz_dir} -DBAZ_COMMIT="${baz_c0}" \
	-DBAZ1_COMMIT="${baz_c2}" \
	-DFOO_DIR=${foo_dir} -DFOO_COMMIT="${foo_c0}" -vvv
expect_exist layers
run_bob layers update -DBAR_1_COMMIT=${bar_c0} -DBAR_2_COMMIT=${bar_c1} -DBAR_DIR=${bar_dir} \
	-DBAZ_DIR=${baz_dir} -DBAZ_COMMIT="${baz_c0}" \
	-DBAZ1_COMMIT="${baz_c2}" \
	-DFOO_DIR=${foo_dir} -DFOO_COMMIT="${foo_c0}"
popd
expect_not_exist layers

# if the layer already exists we fail
mkdir -p layers/bar

expect_fail run_bob layers update -DBAR_1_COMMIT=${bar_c0} -DBAR_2_COMMIT=${bar_c1} -DBAR_DIR=${bar_dir} \
	-DBAZ_DIR=${baz_dir} -DBAZ_COMMIT="${baz_c0}" \
	-DBAZ1_COMMIT="${baz_c2}" \
	-DFOO_DIR=${foo_dir} -DFOO_COMMIT="${foo_c0}"

rm -rf layers/bar

# run update
run_bob layers update -DBAR_1_COMMIT=${bar_c0} -DBAR_2_COMMIT=${bar_c1} -DBAR_DIR=${bar_dir} \
	-DBAZ_DIR=${baz_dir} -DBAZ_COMMIT="${baz_c0}" \
	-DBAZ1_COMMIT="${baz_c2}" \
	-DFOO_DIR=${foo_dir} -DFOO_COMMIT="${foo_c0}"

# make some changes in layers
echo "#foo" >> layers/bar/recipes/bar.yaml

run_bob layers status -DBAR_1_COMMIT=${bar_c0} -DBAR_2_COMMIT=${bar_c1} -DBAR_DIR=${bar_dir} \
	-DBAZ_DIR=${baz_dir} -DBAZ_COMMIT="${baz_c0}" \
	-DBAZ1_COMMIT="${baz_c2}" \
	-DFOO_DIR=${foo_dir} -DFOO_COMMIT="${foo_c0}" | tee log-status.txt
grep -q 'STATUS.\+M.\+[/\]bar' log-status.txt 

# update bar to new revision (bar will be moved to attic)
run_bob layers update -DBAR_1_COMMIT=${bar_c1} -DBAR_2_COMMIT=${bar_c1} -DBAR_DIR=${bar_dir} \
	-DBAZ_DIR=${baz_dir} -DBAZ_COMMIT="${baz_c0}" \
	-DBAZ1_COMMIT="${baz_c2}" \
	-DFOO_DIR=${foo_dir} -DFOO_COMMIT="${foo_c0}"
expect_exist layers.attic/*_bar

bar_now=$(git -C layers/bar rev-parse HEAD)
expect_equal ${bar_c1} ${bar_now}

rm layers.attic -rf
# checkout new foo where the baz* layers have been removed. they should go to attic
run_bob layers update -DBAR_1_COMMIT=${bar_c1} -DBAR_2_COMMIT=${bar_c1} -DBAR_DIR=${bar_dir} \
	-DFOO_DIR=${foo_dir} -DFOO_COMMIT="${foo_c1}"

expect_exist layers.attic/*_baz
expect_exist layers.attic/*_baz1

# test layersScmOverrides
run_bob layers update -DBAR_1_COMMIT=${bar_c1} -DBAR_2_COMMIT=${bar_c1} -DBAR_DIR=${bar_dir} \
	-DFOO_DIR=${foo_dir} -DFOO_COMMIT="${foo_c1}" \
	-lc layers_overrides -vv
expect_exist layers/foo/override

# test that layers status/update are rejected on the old managedLayers policy
old_dir="$tmp_dir/legacy"
mkdir -p "$old_dir/recipes"
expect_fail run_bob -C "$old_dir" layers update
expect_fail run_bob -C "$old_dir" layers status

# remove layers + clean
cleanup
rm -rf layers
