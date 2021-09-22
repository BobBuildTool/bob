#!/bin/bash -e
. ../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }
cleanup

cat >config.yaml <<EOF
bobMinimumVersion: "0.19"
EOF
mkdir -p recipes
cat >recipes/root.yaml <<EOF
root: True
Property: 1
EOF

# Running without "properties" plugin fails because "Property" is unknown
expect_fail run_bob ls

# Running again with the "properties" plugin will work
cat >config.yaml <<EOF
bobMinimumVersion: "0.19"
plugins: [ "properties" ]
EOF
run_bob ls

# Removing the "properties" plugin will make it fail again
cat >config.yaml <<EOF
bobMinimumVersion: "0.19"
EOF
expect_fail run_bob ls

#########################################

cleanup

# Running with "properties" plugin works
cat >config.yaml <<EOF
bobMinimumVersion: "0.19"
plugins: [ "properties" ]
EOF
run_bob ls

# Adding the config file with the "Settings" key but without "settings" plugin fails
expect_fail run_bob ls -c cfg

# Adding the "settings" plugin will work then
cat >config.yaml <<EOF
bobMinimumVersion: "0.19"
plugins: [ "properties", "settings" ]
EOF
run_bob ls -c cfg

# Removing the "settings" plugin will make it fail again
cat >config.yaml <<EOF
bobMinimumVersion: "0.19"
plugins: [ "properties" ]
EOF
expect_fail run_bob ls -c cfg
