# Check environment
if test "$(type -t run_bob)" != function; then
  echo "Please run me via run-tests.sh" >&2
  exit 1
fi

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
