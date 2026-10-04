#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."
cleanup

# FIXME: fails on native windows due to CRLF and qt-creator
if is_win32 ; then
	skip
fi

# Test that all commands working on packages accept arguments which can influence the
# package stack. These are -D, -c and the sandbox modes at the moment.
SANDBOX_MODES=( "" --sandbox --slim-sandbox --dev-sandbox --strict-sandbox --no-sandbox )
COMMANDS=( build clean dev graph ls ls-recipes project query-meta query-path
           query-recipe query-scm show status )

test_command()
{
	case "$1" in
		clean | ls-recipes)
			run_bob "$@" -DBAR=1 -c testconfig
			;;
		project)
			run_bob "$@" -DBAR=1 -c testconfig qt-creator root --kit=none
			;;
		graph)
			run_bob "$@" -DBAR=1 -c testconfig -t dot root $2
			run_bob "$@" -DBAR=1 -c testconfig -t d3 -o d3.showScm=true root
			;;
		*)
			run_bob "$@" -DBAR=1 -c testconfig root
			;;
	esac
}

for CMD in "${COMMANDS[@]}"; do
	for SBX in "${SANDBOX_MODES[@]}" ; do
		test_command "$CMD" $SBX
	done
done
