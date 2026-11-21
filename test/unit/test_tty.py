# Bob build tool
# Copyright (C) 2026  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from unittest import TestCase
from unittest.mock import MagicMock, patch
import io
import os

from bob import tty
from bob.tty import Info, Warn, WarnOnce

class TestShow(TestCase):
    """Warnings and infos must be routed through the current TUI"""

    def testWarnUsesTui(self):
        ui = MagicMock()
        with patch.object(tty, "__tui", ui):
            Warn("foo", help="bar").warn("loc")
        ui.message.assert_called_once()
        msg = ui.message.call_args.args[0]
        self.assertEqual(msg, "WARNING: loc: foo\nbar")

    def testWarnOnceOnlyOnce(self):
        ui = MagicMock()
        w = WarnOnce("foo")
        with patch.object(tty, "__tui", ui):
            w.warn()
            w.warn()
        ui.message.assert_called_once()

    def testInfoRepeats(self):
        ui = MagicMock()
        i = Info("foo")
        with patch.object(tty, "__tui", ui):
            i.show()
            i.show()
        self.assertEqual(ui.message.call_count, 2)

    def testDefaultOnStderr(self):
        stderr = io.StringIO()
        stdout = io.StringIO()
        with patch.object(tty, "__tui", tty.SingleTUI(tty.NORMAL)), \
             patch("sys.stderr", stderr), patch("sys.stdout", stdout):
            Warn("foo", help="bar").warn()
        self.assertEqual(stderr.getvalue(), "WARNING: foo\nbar\n")
        self.assertEqual(stdout.getvalue(), "")

class TestParallelTtyUI(TestCase):

    def testMessageKeepsFooter(self):
        """Messages are printed above the footer and erase the old one"""
        with patch.object(tty.ParallelTtyUI, "_ParallelTtyUI__ttyInit"), \
             patch.object(tty, "terminalSize", os.terminal_size((20, 10)), create=True):
            ui = tty.ParallelTtyUI(tty.NORMAL, 2, MagicMock())
            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch.object(tty, "__tui", ui), \
                 patch("sys.stdout", stdout), patch("sys.stderr", stderr):
                Warn("foo", help="bar").warn()

        self.assertEqual(stderr.getvalue(), "")
        out = stdout.getvalue()
        self.assertTrue(out.startswith("\r\x1b[2KWARNING: foo\x1b[K\n"
                                       "\r\x1b[2Kbar\x1b[K\n"), repr(out))
        # Footer is redrawn afterwards and cursor moved back to its top
        self.assertIn("╭", out)
        self.assertTrue(out.endswith("\x1b[1A\x1b[?7h\r"), repr(out))
