#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }
cleanup

trap 'rm -rf "${tmpDir}"' EXIT
tmpDir=$(mktemp -d)
shareDir="$tmpDir/shared"
cfg="$tmpDir/cfg"

writeCfg()
{
cat >"${cfg}.yaml" <<EOF
share:
    path: "$(mangle_path "$shareDir")"
    quota: $1
    autoClean: $2
EOF
}

case $TEST_ENVIRONMENT in
	posix)
		smallBuildId="76/44/a9c2e7e8c3303374fcbf4da9b0a434f303ea"
		mediumBuildId="05/2a/4d70121ca8e44cd6a9ae77644318d380572a"
		bigBuildId="c7/d7/bc0b590dc216b9a25a525a4afe2de6c4815f"
		;;
	msys)
		smallBuildId="1a/f9/efe8cb45cdcac23693376d7844be208a51c1"
		mediumBuildId="db/a8/c02aaaa7b040c94b1273fc9e48638ba48ef8"
		bigBuildId="13/56/0ebc3b7bce75006e539a584548f5acd9a011"
		;;
	win32)
		smallBuildId="e6/3e/a95d69feee555aff8439a878bf8d0ee5df18"
		mediumBuildId="d1/51/0e715963280cd98c5c61ca77cf27afcf139a"
		bigBuildId="77/a1/99907bc7da53d70b20b1beccba78e99bb354"
		;;
esac

# After installing a package the auto-gc will remove an unsed package if
# installing a new one.
writeCfg '1024' "True"

run_bob dev -c "$cfg" dev root-small
expect_exist "$shareDir/$smallBuildId-3/workspace/result.txt"

rm -rf dev
run_bob dev -c "$cfg" dev root-medium
expect_not_exist "$shareDir/$smallBuildId-3/workspace/result.txt"
expect_exist "$shareDir/$mediumBuildId-3/workspace/result.txt"

# Low quota but no auto-gc. All packages are still installed.
writeCfg '"2K"' "False"
rm -rf dev
run_bob dev -c "$cfg" dev root-big
expect_exist "$shareDir/$mediumBuildId-3/workspace/result.txt"
expect_exist "$shareDir/$bigBuildId-3/workspace/result.txt"

# Start from scratch. Make sure installed packages are referenced.
writeCfg '"5G"' "False"
rm -rf dev "$shareDir"
run_bob dev -c "$cfg" dev root-medium
run_bob dev -c "$cfg" dev root-big

# Enable auto-gc and install another package. Because everything is still used
# no package will be garbage collected.
writeCfg '"2048"' "True"
run_bob dev -c "$cfg" dev root-small
expect_exist "$shareDir/$smallBuildId-3/workspace/result.txt"
expect_exist "$shareDir/$mediumBuildId-3/workspace/result.txt"
expect_exist "$shareDir/$bigBuildId-3/workspace/result.txt"

# Explicitly cleaning unused packages does nothing.
run_bob clean -c "$cfg" --shared
expect_exist "$shareDir/$smallBuildId-3/workspace/result.txt"
expect_exist "$shareDir/$mediumBuildId-3/workspace/result.txt"
expect_exist "$shareDir/$bigBuildId-3/workspace/result.txt"

# Explicitly cleaning shared packages that are still used removes them. The oldest
# packages are removed first. (medium - big - small)
writeCfg '"64KiB"' "True"
run_bob clean -c "$cfg" --shared --used
expect_exist "$shareDir/$smallBuildId-3/workspace/result.txt"
expect_not_exist "$shareDir/$mediumBuildId-3/workspace/result.txt"
expect_not_exist "$shareDir/$bigBuildId-3/workspace/result.txt"

# Normally unused packages are not removed if the quota is not exceeded
writeCfg '"1MB"' "True"
run_bob dev -c "$cfg" dev '*'
rm -rf dev
run_bob clean -c "$cfg" --shared
expect_exist "$shareDir/$smallBuildId-3/workspace/result.txt"
expect_exist "$shareDir/$mediumBuildId-3/workspace/result.txt"
expect_exist "$shareDir/$bigBuildId-3/workspace/result.txt"

# But adding --unused to "clean --shared" will unconditionally remove them.
# This works even without quota. First try with --dry-run then do the real
# delete.
writeCfg 'null' "False"

run_bob clean -c "$cfg" --shared --all-unused --dry-run
expect_exist "$shareDir/$smallBuildId-3/workspace/result.txt"
expect_exist "$shareDir/$mediumBuildId-3/workspace/result.txt"
expect_exist "$shareDir/$bigBuildId-3/workspace/result.txt"

run_bob clean -c "$cfg" --shared --all-unused
expect_not_exist "$shareDir/$smallBuildId-3/workspace/result.txt"
expect_not_exist "$shareDir/$mediumBuildId-3/workspace/result.txt"
expect_not_exist "$shareDir/$bigBuildId-3/workspace/result.txt"
