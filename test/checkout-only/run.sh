# Test that --checkout-only does what it says. Additionally it checks that
# tools that are used during a checkout step are still built.

# Check environment
if test "$(type -t run_bob)" != function; then
  echo "Please run me via run-tests.sh" >&2
  exit 1
fi

# checkout sources
run_bob dev root --checkout-only

# compare result
diff -Nurp output/app "$(run_bob query-path -f '{src}' root/app)"
diff -Nurp output/lib "$(run_bob query-path -f '{src}' root/app/lib)"
