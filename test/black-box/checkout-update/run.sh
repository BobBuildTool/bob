#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }
cleanup

# Test that on --build-only runs, the checkout scripts that are enabled via
# checkoutUpdateIf.

# checkout sources and remember state of deterministic checkout
run_bob dev deterministic-root --build-only
read -r CNT_ROOT_BASE < dev/src/deterministic-root/1/workspace/root.txt
read -r CNT_NO_UPDATE_BASE < dev/src/deterministic-root/1/workspace/no-update.txt

# Running a deterministic checkout again does not update
run_bob dev deterministic-root --build-only
read -r CNT_ROOT_NEW < dev/src/deterministic-root/1/workspace/root.txt
read -r CNT_NO_UPDATE_NEW < dev/src/deterministic-root/1/workspace/no-update.txt
expect_equal "$CNT_ROOT_BASE" "$CNT_ROOT_NEW"
expect_equal "$CNT_NO_UPDATE_BASE" "$CNT_NO_UPDATE_NEW"

# Changing the workspace and running deterministic checkout again does trigger
# an update.
echo foo > dev/src/deterministic-root/1/workspace/bar.txt
run_bob dev deterministic-root --build-only
read -r CNT_ROOT_NEW < dev/src/deterministic-root/1/workspace/root.txt
read -r CNT_NO_UPDATE_NEW < dev/src/deterministic-root/1/workspace/no-update.txt
expect_not_equal "$CNT_ROOT_BASE" "$CNT_ROOT_NEW"
expect_equal "$CNT_NO_UPDATE_BASE" "$CNT_NO_UPDATE_NEW"


# checkout sources and remember state of indeterministic checkout
run_bob dev indeterministic-root --build-only
read -r CNT_ROOT_BASE < dev/src/indeterministic-root/1/workspace/root.txt
read -r CNT_NO_UPDATE_BASE < dev/src/indeterministic-root/1/workspace/no-update.txt

# Running an indeterministic checkout again does update
run_bob dev indeterministic-root --build-only
read -r CNT_ROOT_NEW < dev/src/indeterministic-root/1/workspace/root.txt
read -r CNT_NO_UPDATE_NEW < dev/src/indeterministic-root/1/workspace/no-update.txt
expect_not_equal "$CNT_ROOT_BASE" "$CNT_ROOT_NEW"
expect_equal "$CNT_NO_UPDATE_BASE" "$CNT_NO_UPDATE_NEW"
CNT_ROOT_BASE="$CNT_ROOT_NEW"

# Changing the workspace and running deterministic checkout again does trigger
# an update.
echo foo > dev/src/indeterministic-root/1/workspace/bar.txt
run_bob dev indeterministic-root --build-only
read -r CNT_ROOT_NEW < dev/src/indeterministic-root/1/workspace/root.txt
read -r CNT_NO_UPDATE_NEW < dev/src/indeterministic-root/1/workspace/no-update.txt
expect_not_equal "$CNT_ROOT_BASE" "$CNT_ROOT_NEW"
expect_equal "$CNT_NO_UPDATE_BASE" "$CNT_NO_UPDATE_NEW"
