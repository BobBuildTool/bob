# Bob build tool
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This is the entry point for Phyton IDEs like PhyDev.
# It needs to be in a separate package to get relative path working,
# wich are used in bob.
#
# Phydev debug configuration setup:
#  - Main Module: debug.py
#  - Workspace:   recipe repo, e.g. sandbox
#  - Arguments:   bob args, e.g. dev vexpress

from bob.scripts import bob
import sys,os

if __name__ == '__main__':
    rootDir = os.getcwd()
    bob(rootDir)

