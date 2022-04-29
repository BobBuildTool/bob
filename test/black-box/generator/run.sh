#!/bin/bash -e
. ../../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }
cleanup

# just generate
run_bob project -n g1 root > log-cmd.txt
diff -u <(grep '^PLUGIN' log-cmd.txt) output-plugin.txt

# run and generate
run_bob project qt-creator root --kit 'dummy' > log-cmd.txt
RES=$(sed -ne '/^Build result is in/s/.* //p' log-cmd.txt)
diff -Nurp $RES output

# run and generate
run_bob project eclipseCdt root > log-cmd.txt
RES=$(sed -ne '/^Build result is in/s/.* //p' log-cmd.txt)
diff -Nurp $RES output

# run and generate for vscode
run_bob project vscode root > log-cmd.txt
RES=$(sed -ne '/^Build result is in/s/.* //p' log-cmd.txt)
diff -Nurp $RES output

# Just generate a Visual Studio project. It wont's work anyway on unix.
run_bob project -n __vs2019 root --name vs2019
