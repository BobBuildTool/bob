#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

foo_dir=$(mktemp -d)
bar_dir=$(mktemp -d)
baz_dir=$(mktemp -d)
trap 'rm -rf "$foo_dir" "$bar_dir" "$baz_dir" layers layers.attic log-status.txt' EXIT
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
popd # $baz_dir

# just build the root recipe. Layer should be fetched automatically.
run_bob dev root -DBAR_1_COMMIT=${bar_c0} -DBAR_2_COMMIT=${bar_c1} -DBAR_DIR=${bar_dir} -DBAZ_DIR=${baz_dir} -DBAZ_COMMIT="${baz_c0}" -DFOO_DIR=${foo_dir} -vvv

# run update
run_bob layers update -DBAR_1_COMMIT=${bar_c0} -DBAR_2_COMMIT=${bar_c1} -DBAR_DIR=${bar_dir} \
	-DBAZ_DIR=${baz_dir} -DBAZ_COMMIT="${baz_c0}" -DFOO_DIR=${foo_dir}

# remove layers + clean
cleanup
rm -rf layers

# run update - should fetch layers
run_bob layers update -DBAR_1_COMMIT=${bar_c0} -DBAR_2_COMMIT=${bar_c1} -DBAR_DIR=${bar_dir} \
	-DBAZ_DIR=${baz_dir} -DBAZ_COMMIT="${baz_c0}" -DFOO_DIR=${foo_dir}

# make some changes in layers
echo "#foo" >> layers/bar/recipes/bar.yaml

run_bob layers status -DBAR_1_COMMIT=${bar_c0} -DBAR_2_COMMIT=${bar_c1} -DBAR_DIR=${bar_dir} \
	-DBAZ_DIR=${baz_dir} -DBAZ_COMMIT="${baz_c0}" -DFOO_DIR=${foo_dir} | tee log-status.txt
grep -q 'STATUS.\+M.\+[/\]bar' log-status.txt 

# update bar to new revision (bar will be moved to attic)
run_bob layers update -DBAR_1_COMMIT=${bar_c1} -DBAR_2_COMMIT=${bar_c1} -DBAR_DIR=${bar_dir} \
	-DBAZ_DIR=${baz_dir} -DBAZ_COMMIT="${baz_c0}" -DFOO_DIR=${foo_dir}
expect_exist layers.attic

bar_now=$(git -C layers/bar rev-parse HEAD)
expect_equal ${bar_c1} ${bar_now}
