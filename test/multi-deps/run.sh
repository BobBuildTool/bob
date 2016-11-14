# Check environment
if test "$(type -t run_bob)" != function; then
  echo "Please run me via run-tests.sh" >&2
  exit 1
fi

exec_blackbox_test
