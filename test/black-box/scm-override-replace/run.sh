#!/bin/bash -e
#
#  Test string substitution of scmOverrides 'replace' entries. The replacement
#  is always substituted, the pattern only with 'substituteReplace'.
#
source "$(dirname "$0")/../../test-lib.sh" "../../.."
cleanup

query_url()
{
	run_bob query-scm "$@" root | cut -d' ' -f4
}

# pattern and replacement substituted, back-reference preserved
test "$(query_url -DHOST=old.example.com -DOLD_HOST=old.example.com -DNEW_HOST=new.example.com)" \
	= "https://new.example.com/path/file.tar.gz"

# non-matching pattern after substitution -> url unchanged
test "$(query_url -DHOST=old.example.com -DOLD_HOST=foo.example.com -DNEW_HOST=new.example.com)" \
	= "https://old.example.com/path/file.tgz"

# second override: no substitution at all, '$' anchor works verbatim
test "$(query_url -DHOST=unrelated.example.com -DOLD_HOST=x -DNEW_HOST=x -DEXT=zip)" \
	= 'https://unrelated.example.com/path/file.${EXT}'

# undefined variables are an error
expect_fail run_bob query-scm -DHOST=old.example.com root
