#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."
cleanup

# The plugin provided 'hello' command is dispatched like any built-in command
# and receives the generated package graph. Smoke test getRootPackage(),
# queryPackagePath() and getAliases() on the passed PackageSet.
run_bob hello root --additional options | tee log-cmd.txt
diff -u output-plain.txt log-cmd.txt

# Standard arguments (-D, -c, sandbox mode) are consumed by Bob and must not
# reach the plugin's argv.
run_bob hello -D FOO=bar root --additional options --sandbox | tee log-cmd.txt
diff -u output-sandbox.txt log-cmd.txt

# An unknown command must still yield the usual error, not a crash.
expect_fail --code=2 run_bob nosuchcommand
