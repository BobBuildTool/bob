#!/bin/sh
cd "${0%/*}"
export PYTHONPATH="${PWD}/../pym"
python3 -m unittest
