#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."

# Verify the properties of setup scripts.

# The checkoutSetup and buildSetup from inert class must not be executed.
cleanup
run_bob dev root
expect_exist dev/dist/root/1/workspace/canary.txt

# Verify that packageSetup is included in interactive shell environment. Need
# to use "script" to fake a tty. Otherwise the rcfile would not be loaded and
# the shell envirionment is not setup.
if [[ $(uname -o) != Msys ]] ; then
	rm dev/dist/root/1/workspace/canary.txt
	script -qfec "./dev/dist/root/1/package.sh shell" /dev/null <<'EOF'
foo
exit
EOF
	expect_exist dev/dist/root/1/workspace/canary.txt
fi
