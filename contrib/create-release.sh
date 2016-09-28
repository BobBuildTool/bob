#!/bin/bash

set -e
set -o nounset

# show help if called without argument
if [[ $# -lt 1 ]] ; then
	cat <<EOF

NAME
	create-release.sh

SYNOPSIS
	create-release.sh <package>

DESCRIPTION
	Prepares a release by amending all git SCMs with their current commit id.
	The affected recipes and/or classes are edited in-place. Entries that have
	already a commit or tag are skipped.

	The script utilizes the "ruamel.yaml" python module to patch the recipes.
	While it preserves the general structure and comments it does not do so
	with the indentation. Expect white space changes...

EOF
	exit 0
fi

# arguments: $1:RECIPE $2:COMMIT
apply_commit()
{
	python3 - <<EOF
import ruamel.yaml
import sys

(fileName, idx) = "$1".split('#')
idx = int(idx)

with open(fileName, "r") as f:
	recipe = ruamel.yaml.round_trip_load(f.read())

scms = recipe["checkoutSCM"]
if isinstance(scms, list):
	scms[idx]["commit"] = "$2"
else:
	scms["commit"] = "$2"

with open(fileName, "w") as f:
	f.write(ruamel.yaml.round_trip_dump(recipe, indent=3,
		block_seq_indent=1, default_flow_style=False))
EOF
}

bob query-scm $1 -r \
	--default UNDEFINED \
	-f "git=git {recipe} {url} {branch} {tag} {commit}" \
| grep '^git' \
| while read TYPE RECIPE URL BRANCH TAG COMMIT ; do
	if [[ $COMMIT != UNDEFINED || $TAG != UNDEFINED ]] ; then
		echo "Skipping $URL! Already on tag/commit..."
	else
		COMMIT="$(git ls-remote $URL refs/heads/$BRANCH)"
		COMMIT="${COMMIT:0:40}"
		apply_commit $RECIPE $COMMIT
	fi
done
