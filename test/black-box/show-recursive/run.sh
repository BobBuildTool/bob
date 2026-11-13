#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."

out=$(run_bob show --format diff --recursive recursive-left recursive-right)
printf '%s\n' "$out" | grep -Fx 'diff recursive-left recursive-right'
printf '%s\n' "$out" | grep -Fx -- '--- recursive-left'
printf '%s\n' "$out" | grep -Fx -- '+++ recursive-right'
printf '%s\n' "$out" | grep -F "caused by 'depends[0]'"
printf '%s\n' "$out" | grep -Fx 'diff recursive-left/middle/leaf recursive-right/middle/leaf'
printf '%s\n' "$out" | grep -F -- '- packageVars.VARIANT=left'
printf '%s\n' "$out" | grep -F -- '+ packageVars.VARIANT=right'
out=$(run_bob show --format diff --recursive -f packageVars recursive-left recursive-right)
printf '%s\n' "$out" | grep -F -- '- packageVars.VARIANT=left'
printf '%s\n' "$out" | expect_fail grep -F 'meta.'
out=$(run_bob show --format diff --recursive --show-common -f packageVars recursive-left recursive-right)
printf '%s\n' "$out" | grep -F 'packageVars.VARIANT=right'
out=$(run_bob show --format diff --recursive recursive-tool-left recursive-tool-right)
printf '%s\n' "$out" | grep -F "caused by 'packageTools.variant-tool' from package 'variant-tool':"
printf '%s\n' "$out" | grep -Fx 'diff recursive-tool-left/variant-tool recursive-tool-right/variant-tool'
printf '%s\n' "$out" | grep -F -- '- packageVars.VARIANT=left'
out=$(run_bob show --format diff --recursive order-left order-right)
printf '%s\n' "$out" | grep -F 'depends[0]=order-left/a'
printf '%s\n' "$out" | grep -F 'depends[0]=order-right/b'
out=$(run_bob show --format diff --recursive script-left script-left)
[ -z "$out" ]

# Equal dependency variants have a common path in the recursive diff.
out=$(run_bob show --format diff --recursive -f depends -f buildVars common-dep-left common-dep-right)
printf '%s\n' "$out" | grep -F -- '- buildVars.VARIANT=left'
printf '%s\n' "$out" | expect_fail grep -F 'depends[0]'
out=$(run_bob show --format diff --recursive --show-common -f depends -f buildVars common-dep-left common-dep-right)
printf '%s\n' "$out" | grep -F '  depends[0]=common-dep-left/a'
printf '%s\n' "$out" | expect_fail grep -F 'depends[0]=common-dep-right/a'
