#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

# Smoke test to see if all output formats are not crashing
run_bob show something-invalid
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

# Verify that empty properties are hidden by default but can be activated
run_bob show root --no-indent | python3 -c '
import sys, yaml
d = yaml.load(sys.stdin.read(), Loader=yaml.Loader)
assert "checkoutTools" not in d
'

run_bob show root --show-empty | python3 -c '
import sys, yaml
d = yaml.load(sys.stdin.read(), Loader=yaml.Loader)
assert d["checkoutTools"] == {}
'

# Verify that filtering works as expected
run_bob show root -f buildVars -f packageVars | python3 -c '
import sys, yaml
d = yaml.load(sys.stdin.read(), Loader=yaml.Loader)
assert set(d.keys()) == {"buildVars", "packageVars"}
assert set(d["buildVars"].keys()) == {"FOO", "BAR"}
assert set(d["packageVars"].keys()) == {"FOO", "BAR", "META"}
'
