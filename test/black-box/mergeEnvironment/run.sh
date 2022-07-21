#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }
cleanup

# test disabled policy
cat >config.yaml <<EOF
policies:
    mergeEnvironment: False
EOF
run_bob dev root
diff -uZ output/disabled.txt "$(run_bob query-path -f '{dist}' root)/result.txt"

# test enabled policy
cat >config.yaml <<EOF
policies:
    mergeEnvironment: True
EOF
run_bob dev root
diff -uZ output/enabled.txt "$(run_bob query-path -f '{dist}' root)/result.txt"
