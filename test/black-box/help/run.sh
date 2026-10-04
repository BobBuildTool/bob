#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."
cleanup empty build

# Plain 'bob help' is the same as 'bob --help'. It lists the built-in commands
# but no plugin provided commands.
run_bob help > log-cmd.txt
diff -u <(run_bob --help) log-cmd.txt
grep -q '^  build ' log-cmd.txt
grep -q '^  query-path ' log-cmd.txt
expect_fail grep -q 'plugin defined commands' log-cmd.txt
expect_fail grep -q 'hello' log-cmd.txt

# 'bob help -a' additionally lists the plugin provided commands, sorted by
# name and with their help text aligned to the built-in commands.
run_bob help -a > log-cmd.txt
grep -q '^  build ' log-cmd.txt
grep -q '^  query-path ' log-cmd.txt
sed -n '/plugin defined commands/,$p' log-cmd.txt | diff -u output-plugins.txt -
run_bob help --all | diff -u log-cmd.txt -

# Help for a plugin command prints its help text instead of a man page.
expect_output "'hello' is provided by a plugin: Example plugin command" \
	run_bob help hello
expect_output "'nohelp' is provided by a plugin: " run_bob help nohelp
expect_output "'bare' is provided by a plugin: " run_bob help bare

# Outside of a project there are no plugin commands but listing them must not
# fail.
mkdir -p empty
run_bob -C empty help -a > log-cmd.txt
grep -q '^  build ' log-cmd.txt
expect_equal "$(sed -n '/plugin defined commands/,$p' log-cmd.txt)" \
	"The following plugin defined commands are available:"

# Plugin commands of the project are also listed in out-of-tree build
# directories.
run_bob init . build
run_bob -C build help -a > log-cmd.txt
sed -n '/plugin defined commands/,$p' log-cmd.txt | diff -u output-plugins.txt -
