#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

# Check that we can build without an audit trail. Such artifacts must not be
# uploaded. Artifacts that depend on another without audit trail must also not
# have an audit trail but must build successfully.

cleanup
rm -rf default.yaml

# setup local archive
archiveDir=$(mktemp -d)
trap 'rm -rf "${archiveDir}"' EXIT
cat >default.yaml <<EOF
archive:
  -
    backend: file
    path: "$(mangle_path "$archiveDir")"
EOF

# Build dependency without audit trail first. Must not be uploaded.
run_bob dev -v --upload --no-audit root/dep
test -z "$(find "$archiveDir" -type f -name '*.tgz')"

# Build dependent package next with audit trail. Must not be uploaded either
# because dependency audit trail is missing.
run_bob dev -v --upload --audit -n root
test -z "$(find "$archiveDir" -type f -name '*.tgz')"

# Rebuild forced and upload again. Now it must create artifcats in the archive.
run_bob dev -v --upload -f root
test -n "$(find "$archiveDir" -type f -name '*.tgz')"
