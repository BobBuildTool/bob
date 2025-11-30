#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."

# Smoke test to see if all output formats are not crashing
run_bob --query=nullset show something-invalid
run_bob show root --format=yaml --indent=2
run_bob show root --format=json --no-indent
run_bob show root --format=flat
run_bob show "//*" --format=flat
run_bob show --sandbox root/tool root/sandbox --format=diff

# The diff format expects two packages. Otherwise it must fail
expect_fail run_bob show root --format=diff

# Normally common lines are suppressed in diff format. Can be enabled
# selectively, though.
run_bob show --format diff root/dep/ root/sandbox/ | expect_fail grep -q scriptLanguage
run_bob show --format diff --show-common root/dep/ root/sandbox/ | grep -q scriptLanguage

if is_win32; then
	PYTHON=python
else
	PYTHON=python3
fi

# Verify that empty properties are hidden by default but can be activated
run_bob show root --no-indent | $PYTHON -c '
import sys, yaml
d = yaml.load(sys.stdin.read(), Loader=yaml.Loader)
assert "checkoutTools" not in d
'

run_bob show root --show-empty | $PYTHON -c '
import sys, yaml
d = yaml.load(sys.stdin.read(), Loader=yaml.Loader)
assert d["checkoutTools"] == {}
'

# Verify that filtering works as expected
run_bob show root -f buildVars -f packageVars | $PYTHON -c '
import sys, yaml
d = yaml.load(sys.stdin.read(), Loader=yaml.Loader)
assert set(d.keys()) == {"buildVars", "packageVars"}
assert set(d["buildVars"].keys()) == {"FOO", "BAR"}
assert set(d["packageVars"].keys()) == {"FOO", "BAR", "META"}
'
