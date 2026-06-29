#!/usr/bin/env python3

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock


TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_ROOT / "mcp"))

import win_remote_mcp as mcp  # noqa: E402


class WinRemoteMcpTests(unittest.TestCase):
    def test_v3_transport_is_default(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(mcp.use_v3_transport())

    def test_legacy_transport_escape_hatches(self) -> None:
        for value in ["legacy", "v2"]:
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {"WIN_REMOTE_MCP_TRANSPORT": value}, clear=True):
                    self.assertFalse(mcp.use_v3_transport())

    def test_v3_transport_accepts_empty_and_explicit_v3(self) -> None:
        for value in ["", "v3", " V3 "]:
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {"WIN_REMOTE_MCP_TRANSPORT": value}, clear=True):
                    self.assertTrue(mcp.use_v3_transport())

    def test_unknown_transport_is_rejected(self) -> None:
        with mock.patch.dict(os.environ, {"WIN_REMOTE_MCP_TRANSPORT": "shell"}, clear=True):
            with self.assertRaisesRegex(ValueError, "WIN_REMOTE_MCP_TRANSPORT"):
                mcp.use_v3_transport()


if __name__ == "__main__":
    unittest.main()
