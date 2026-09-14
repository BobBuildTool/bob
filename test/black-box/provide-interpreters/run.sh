#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."

# provideInterpreters smoke test. Symlink the system installed interpreter
# and compare the used argv[0] in the user to the symlink.

cleanup

check_lang()
{
	local lang="$1"
	local real used

	run_bob dev "$lang-consumer"

	res=$(< "dev/dist/$lang-consumer/1/workspace/result.txt")
	expect_equal "$res" interposer
}

check_lang bash
check_lang python
if [[ $(uname -s) == Linux ]] && type -p pwsh >/dev/null 2>&1 || is_win32 ; then
    check_lang pwsh
fi
