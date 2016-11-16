#!/bin/bash -e
. ../test-lib.sh 2>/dev/null || { echo "Must run in script directory!" ; exit 1 ; }
cleanup

# just generate
run_bob project -n g1 root > log.txt
diff -u <(grep '^PLUGIN' log.txt) output-plugin.txt

# run and generate
run_bob project qt-creator root --kit 'dummy' > log.txt
RES=$(sed -ne '/^Build result is in/s/.* //p' log.txt)
diff -Nurp $RES output

# run and generate
run_bob project eclipseCdt root > log.txt
RES=$(sed -ne '/^Build result is in/s/.* //p' log.txt)
diff -Nurp $RES output
