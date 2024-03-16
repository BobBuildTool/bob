#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

if is_msys ; then
	# svnadmin does not work on MSYS
	skip
fi

cleanup
rm -rf user.yaml override.yaml

gitDir=$(mktemp -d)
urlDir=$(mktemp -d)
svnDir=$(mktemp -d)

trap 'rm -rf "${gitDir}" "${urlDir}" "${svnDir}"' EXIT

# init a git - repo
pushd ${gitDir}
git init
git config user.email "bob@bob.bob"
git config user.name test
echo "git" > test.dat
git add test.dat
git commit -m "added test"
popd

# init url repo
echo "url" > ${urlDir}/test2.dat

# init svn repo
pushd $svnDir
mkdir -p trunk
svnadmin create svnTest
echo "svn" > test3.dat
svnUrl="$(file_url "$svnDir")"
svn import test3.dat ${svnUrl}/svnTest/test3.dat -m "Initial import"
popd

# setup user.yaml to provide directories instead of passing them on the command
# line
cat >user.yaml <<EOF
environment:
    REPODIR : "$(file_url "${gitDir}")"
    URLDIR : "$(file_url "${urlDir}")"
    SVNDIR : "${svnUrl}"
EOF

# build once
run_bob dev root

# without modifications 'status' should be silent
expect_output "" run_bob status root -r

# overrides must be shown even if nothing was changed if requested
run_bob status -r --show-overrides root | tee log-status.txt
grep -q 'O.\+[/\]git' log-status.txt
grep -q 'O.\+[/\]svn' log-status.txt
grep -q 'O.\+[/\]url' log-status.txt

# test if expressions on overrides
run_bob status -r --show-overrides -DSCM=url  root | tee log-status.txt
grep -q 'O.\+[/\]git' log-status.txt
if grep -q 'O.\+[/\]svn' log-status.txt; then exit 1; fi
grep -q 'O.\+[/\]url' log-status.txt
run_bob status -r --show-overrides -DSCM=svn  root | tee log-status.txt
grep -q 'O.\+[/\]git' log-status.txt
grep -q 'O.\+[/\]svn' log-status.txt
if grep -q 'O.\+[/\]url' log-status.txt; then exit 1; fi

# Temporary override to simulate recipe changes. Old git/svn directories should
# be moved to attic and new ones are about to be created. We simulate a
# collision in the svn directory. That should be reported.
mkdir "$(run_bob query-path -f '{src}' root/svn)/bar"
cat >override.yaml <<EOF
scmOverrides:
    -
      match:
          scm: git
      set:
          branch: foo
    -
      match:
          scm: svn
      set:
          dir: bar
EOF
run_bob status -r root --show-clean | tee log-status.txt
grep -q 'STATUS.\+AN.\+[/\]git' log-status.txt
grep -q 'STATUS.\+A.\+[/\]svn' log-status.txt
grep -q 'STATUS.\+CN.\+[/\]bar' log-status.txt
rm override.yaml
rmdir "$(run_bob query-path -f '{src}' root/svn)/bar"

# modify git
echo foo >> "$(run_bob query-path -f '{src}' root/git)/git/test.dat"
run_bob status root/git | tee log-status.txt
grep -q 'STATUS.\+M.\+[/\]git' log-status.txt
run_bob status root/git -v | tee log-status.txt
grep -q 'STATUS.\+M.\+[/\]git' log-status.txt
grep -q 'M test.dat' log-status.txt

# modify svn
echo bar >> "$(run_bob query-path -f '{src}' root/svn)/svn/test3.dat"
run_bob status root/svn -vv | tee log-status.txt
grep -q 'STATUS.\+M.\+[/\]svn' log-status.txt
grep -q 'M \+test3.dat' log-status.txt

# call 'status' without package -> list all modified repos
run_bob status | tee log-status.txt
grep -q 'STATUS.\+M.\+[/\]git' log-status.txt
grep -q 'STATUS.\+M.\+[/\]svn' log-status.txt

# "bob dev --clean-checkout" must move modified repos to attic. Validate result!
run_bob dev root --clean-checkout
diff -NurpZ $(run_bob query-path -f '{dist}' root) output
expect_output "" run_bob status root -r

# look for repositories in attic, they should be modified
run_bob status root -r --attic | tee log-status.txt
grep -q 'STATUS.\+M.\+[/\]attic[/\].\+git' log-status.txt
grep -q 'STATUS.\+M.\+[/\]attic[/\].\+svn' log-status.txt

# look for attic without package argument
run_bob status --attic -vv | tee log-status.txt
grep -q 'STATUS.\+M.\+[/\]attic[/\].\+git' log-status.txt
grep -q 'M test.dat' log-status.txt
grep -q 'STATUS.\+M.\+[/\]attic[/\].\+svn' log-status.txt
grep -q 'M \+test3.dat' log-status.txt

# delete everything and do bob status
rm -rf dev
run_bob status root -r -vvv
run_bob status --attic -vvv
