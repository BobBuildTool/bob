#!/bin/bash -e
. ../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }

# init a git - repo
gitDir=$(mktemp -d)
pushd ${gitDir}
git init
git config user.email "bob@bob.bob"
git config user.name test
echo "ok" > test.dat
git add test.dat
git commit -m "added test"
popd
# init url repo
urlDir=$(mktemp -d)
echo "ok" > ${urlDir}/test2.dat
# init svn repo
svnDir=$(mktemp -d)
pushd $svnDir
mkdir -p trunk
svnadmin create svnTest
echo "ok" > test3.dat
svn import test3.dat file://${svnDir}/svnTest/test3.dat -m "Initial import"
popd

run_bob dev root -DREPODIR=${gitDir} -DURLDIR=${urlDir} -DSVNDIR=${svnDir} > log-cmd.txt 2> /dev/null
RES=$(sed -ne '/^Build result is in/s/.* //p' log-cmd.txt)
diff -Nurp $RES output

run_bob status root --show-overrides -DREPODIR=${gitDir} -DURLDIR=${urlDir} -DSVNDIR=${svnDir} | sed -rn 's/.*STATUS *([A-Z]).*dev.*/\1/p' > log-status.txt
diff log-status.txt log-status-ok.txt

rm -rf ${gitDir} ${urlDir} ${svnDir}

