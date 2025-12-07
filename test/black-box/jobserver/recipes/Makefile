.PHONY: all a b c cleanup

all: a b c

# Each target will put its character in the file and, after 1s, add a newline.
# This will create one word when executed in parallel or three words if
# executed serially.
a b c: cleanup
	echo -n "$@" >>result.txt
	sleep 0.5
	echo >>result.txt

cleanup:
	rm -rf result.txt
