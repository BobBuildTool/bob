#!/bin/bash -e
. ../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }
cleanup

# Test that all commands working on packages accect arguments which can influence the
# package stack. These are -D and -c at the moment.

cmds=$(python3 -c "import sys,os
sys.path.append(os.path.join(os.getcwd(), '..', '..', 'pym'))
from bob.scripts import availableCommands
for cmd, (hl, func, help) in sorted(availableCommands.items()):
    print(cmd)")

for c in $cmds; do
	case "$c" in
		archive | jenkins | help | _*)
			;;
        clean)
			run_bob $c -DBAR=1 -c testconfig
			;;
		project)
			run_bob project -DBAR=1 -c testconfig qt-creator root --kit=none
			;;
        graph)
			run_bob $c -DBAR=1 -c testconfig -t dot root
			run_bob $c -DBAR=1 -c testconfig -t d3 root
            ;;
		*)
			run_bob $c -DBAR=1 -c testconfig root
			;;
	esac
done
