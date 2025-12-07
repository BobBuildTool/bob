#!/bin/bash -ex
source "$(dirname "$0")/../../test-lib.sh" "../../.."

# Check jobserver integration. Works only on UNIX systems. We have a couple of
# variation points that are independent of each other:
#
# Jobserver:		internal, external-pipe, external-fifo
# Recipe "jobServer":	True/"pipe", "fifo", "fifo-or-pipe"
# Sandbox:		no sandbox/slim sandbox/strict sandbox

is_posix || skip
cleanup

# Build our own make if the system make is too old...
if [[ ! -d make-4.4.1 ]] ; then
	tar -xf make-4.4.1.tar.xz
fi
if [[ ! -x make-4.4.1/make ]] ; then
	pushd make-4.4.1
	./configure
	./build.sh
	popd
fi

export PATH="$PWD/make-4.4.1:$PATH"
export_run_bob

#
# PIPE-only smoke tests...
#

# Run serially
rm -rf dev
run_bob dev root
expect_equal $(wc -w < dev/dist/root/1/workspace/result.txt) 3

# Run in parallel with internal job server
# internal / "pipe" / no sandbox
rm -rf dev
run_bob dev root -j8
expect_equal $(wc -w < dev/dist/root/1/workspace/result.txt) 1

# Run in parallel with external FIFO job server
# external-fifo / "pipe" / no sandbox
rm -rf dev
make -j8 --jobserver-style=fifo
expect_equal $(wc -w < dev/dist/root/1/workspace/result.txt) 1

# Run in parallel with external PIPE job server
# external-pipe / "pipe" / no sandbox
rm -rf dev
make -j8 --jobserver-style=pipe
expect_equal $(wc -w < dev/dist/root/1/workspace/result.txt) 1

# Run in parallel make but without recursive rule. Must execute serially.
rm -rf dev
make -j8 --jobserver-style=pipe serially
expect_equal $(wc -w < dev/dist/root/1/workspace/result.txt) 3

# Override external job server.
rm -rf dev
make -j8 --jobserver-style=fifo force2
expect_equal $(wc -w < dev/dist/root/1/workspace/result.txt) 2

#
# FIFO-only smoke tests...
#

# internal / "fifo" / no sandbox
rm -rf dev
run_bob dev fifo -j8
expect_equal $(wc -w < dev/dist/fifo/1/workspace/result.txt) 1

# external-fifo / "fifo" / no sandbox
rm -rf dev
make -j8 --jobserver-style=fifo fifo
expect_equal $(wc -w < dev/dist/fifo/1/workspace/result.txt) 1

# external-pipe / "fifo" / no sandbox
rm -rf dev
expect_fail --code=2 make -j8 --jobserver-style=pipe fifo

if "${BOB_ROOT}/bin/bob-namespace-sandbox" -C ; then
	# internal / "fifo" / slim-sandbox
	rm -rf dev
	run_bob dev fifo -j8 --slim-sandbox
	expect_equal $(wc -w < dev/dist/fifo/1/workspace/result.txt) 1

	# external-fifo / "fifo" / slim-sandbox
	rm -rf dev
	make -j8 --jobserver-style=fifo fifo-slim
	expect_equal $(wc -w < dev/dist/fifo/1/workspace/result.txt) 1

	# internal / "fifo" / strict-sandbox
	rm -rf dev
	run_bob dev fifo -j8 --strict-sandbox
	expect_equal $(wc -w < dev/dist/fifo/1/workspace/result.txt) 1

	# external-fifo / "fifo" / strict-sandbox
	rm -rf dev
	make -j8 --jobserver-style=fifo fifo-strict
	expect_equal $(wc -w < dev/dist/fifo/1/workspace/result.txt) 1
fi

#
# FIFO-to-PIPE fallback smoke tests...
#

# external-fifo / "fifo-or-pipe" / no sandbox
rm -rf dev
make -j8 --jobserver-style=fifo fallback
expect_equal $(wc -w < dev/dist/fallback/1/workspace/result.txt) 1

# external-pipe / "fifo-or-pipe" / no sandbox
rm -rf dev
make -j8 --jobserver-style=pipe fallback
expect_equal $(wc -w < dev/dist/fallback/1/workspace/result.txt) 1
