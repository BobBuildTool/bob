#!/bin/bash -e
. ../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

# Verify the properties of setup scripts.

# The checkoutSetup and buildSetup from inert class must not be executed.
cleanup
run_bob dev root
expect_exist dev/dist/root/1/workspace/canary.txt

# Verify that packageSetup is included in interactive shell environment. Need
# to use "script" to fake a tty. Otherwise the rcfile would not be loaded and
# the shell envirionment is not setup.
rm dev/dist/root/1/workspace/canary.txt
script -qfec "./dev/dist/root/1/package.sh shell" /dev/null <<'EOF'
foo
EOF
expect_exist dev/dist/root/1/workspace/canary.txt
