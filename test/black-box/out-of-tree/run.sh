#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

cleanup

# setup external build directory
trap 'rm -rf "${buildDir}"' EXIT
buildDir=$(mktemp -d)
srdDir="$PWD"

# Initialize external build directory inline
mkdir "$buildDir/one"
pushd "$buildDir/one"
run_bob init "$srdDir"
run_bob dev root
read -r resultDir < dev/dist/root/1/workspace/path.txt
[[ $resultDir =~ ${buildDir}/one ]] || die "Result not in build dir"
expect_output "default" cat dev/dist/root/1/workspace/setting.txt
popd
cmp recipes/input.txt "$buildDir/one/dev/dist/root/1/workspace/output.txt"

# Initialize external build directory by Bob
run_bob init . "$buildDir/two"
pushd "$buildDir/two"
cat >default.yaml <<EOF
environment:
    SETTING: override
EOF
run_bob dev root
read -r resultDir < dev/dist/root/1/workspace/path.txt
[[ $resultDir =~ ${buildDir}/two ]] || die "Result not in build dir"
# A default.yaml in the external build directory takes precedence
expect_output "override" cat dev/dist/root/1/workspace/setting.txt
popd

# A build directory cannot be initialized twice
expect_fail run_bob init . "$buildDir/two"

# Initializing the project directory is allowed and does nothing
run_bob init "$PWD"
expect_not_exist .bob-project

# Initializing a build directory if no valid project root is given fails
expect_fail run_bob init /does/not/exist "$buildDir/three"
expect_not_exist "$buildDir/three"

# Fail gracefully if the build directory cannot be created
touch "$buildDir/three"
expect_fail run_bob init . "$buildDir/three"
