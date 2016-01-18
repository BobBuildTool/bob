#!/bin/sh

cd "${0%/*}/.."
export PYTHONPATH="${PWD}/pym"

if [ -n "$(which coverage)" ] ; then
	coverage run --source "./pym" -m unittest discover test
else
	python3 -m unittest discover test
fi
