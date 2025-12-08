.PHONY: all force2 serially fifo fifo-slim fifo-strict fallback
SHELL = /bin/bash

all:
	+run_bob dev root

force2:
	+run_bob dev root -j2

serially:
	run_bob dev root

fifo:
	+run_bob dev fifo

fallback:
	+run_bob dev fallback

fifo-slim:
	+run_bob dev fifo --slim-sandbox

fifo-strict:
	+run_bob dev fifo --strict-sandbox
