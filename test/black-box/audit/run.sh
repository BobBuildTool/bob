#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."

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
    name: "local"
    backend: file
    path: "$(mangle_path "$archiveDir")"
EOF

# Build dependency without audit trail first. Must not be uploaded.
run_bob dev -v --upload --no-audit root/dep
test -z "$(/usr/bin/find "$archiveDir" -type f -name '*.tgz')"

# Build dependent package next with audit trail. Must not be uploaded either
# because dependency audit trail is missing.
run_bob dev -v --upload --audit -n root
test -z "$(/usr/bin/find "$archiveDir" -type f -name '*.tgz')"

# Rebuild forced and upload again. Now it must create artifcats in the archive.
run_bob dev -v --upload -f root
test -n "$(/usr/bin/find "$archiveDir" -type f -name '*.tgz')"

# Verify additional files in audit trail
expect_equal "$(./extract.py dev/dist/root/1/audit.json.gz ROOT)" foo
expect_equal "$(./extract.py dev/dist/root/1/audit.json.gz FOO)" "$(echo foo | base64)"

# Provoke audit errors
expect_fail run_bob dev audit-absolute
expect_fail run_bob dev audit-missing
expect_fail run_bob dev audit-encoding-error
expect_fail run_bob dev audit-invalid-encoding
