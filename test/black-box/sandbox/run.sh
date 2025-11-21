#!/bin/bash -e
source "$(dirname "$0")/../../test-lib.sh" "../../.."

# Run a simple sandbox that mounts the full host. Just see if exeuction works.

cleanup

# Check if namespace feature works on this host.
"${BOB_ROOT}/bin/bob-namespace-sandbox" -C || skip

run_bob build -DFOO=bar root
run_bob dev --sandbox -E root

# Check that we can keep our UID or even be root inside sandbox
run_bob dev --sandbox -c as-self -DEXPECT_UID=$UID root
run_bob dev --sandbox -c as-root -DEXPECT_UID=0 root

# Test properties in- and outside of sandbox image with respect to the various
# modes...

# The plain "--sandbox" mode executes packages without sandbox image unprotected.
# With an image, the step is executed in a container with stable paths.
cleanup
run_bob dev isolation -D CANARY="$PWD/run.sh" --sandbox \
	-D OUTSIDE_ISOLATED=0 -D OUTSIDE_STABLE_PATH=0 \
	-D INSIDE_ISOLATED=1  -D INSIDE_STABLE_PATH=1 -D INSIDE_IMAGE_USED=1

# The slim sandbox mode does not use the sandbox image. It will always use the
# workspace paths but will execute still in an isolated environment.
cleanup
run_bob dev isolation -D CANARY="$PWD/run.sh" --slim-sandbox \
	-D OUTSIDE_ISOLATED=1 -D OUTSIDE_STABLE_PATH=0 \
	-D INSIDE_ISOLATED=1  -D INSIDE_STABLE_PATH=0 -D INSIDE_IMAGE_USED=0

# The dev sandbox mode is like the slim mode, but will use the sandbox image if
# available...
cleanup
run_bob dev isolation -D CANARY="$PWD/run.sh" --dev-sandbox \
	-D OUTSIDE_ISOLATED=1 -D OUTSIDE_STABLE_PATH=0 \
	-D INSIDE_ISOLATED=1  -D INSIDE_STABLE_PATH=0 -D INSIDE_IMAGE_USED=1

# The strict sandbox mode always executes the steps in isolation with stable
# paths. If a sandbox image is available, it is used.
cleanup
run_bob dev isolation -D CANARY="$PWD/run.sh" --strict-sandbox \
	-D OUTSIDE_ISOLATED=1 -D OUTSIDE_STABLE_PATH=1 \
	-D INSIDE_ISOLATED=1  -D INSIDE_STABLE_PATH=1 -D INSIDE_IMAGE_USED=1
