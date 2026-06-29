#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_ROOT / "mcp"))

import win_remote_mcp as mcp  # noqa: E402


class WinRemoteMcpTests(unittest.TestCase):
    def make_target(self) -> mcp.cli.Target:
        return mcp.cli.Target(
            name="TEST",
            env_file=Path("test.env"),
            host="100.64.1.2",
            user="Administrator",
        )

    def make_call(self, target: mcp.cli.Target, *, ok: bool = True) -> mcp.v3.RpcCall:
        return mcp.v3.RpcCall(
            target=target,
            request={"id": "req-1", "action": "process.capture"},
            response={"id": "req-1", "ok": ok, "exitCode": 0 if ok else 2, "stdoutText": "OK\n"},
            argv=["ssh", target.ssh_destination, "cmd.exe /c rpc-stdio"],
            ssh_returncode=0 if ok else 2,
            ssh_stderr="",
        )

    def test_server_has_no_transport_escape_hatch(self) -> None:
        self.assertEqual(mcp.SERVER_VERSION, "0.3.0")
        self.assertFalse(hasattr(mcp, "use" + "_v3_transport"))
        self.assertFalse(hasattr(mcp, "run" + "_win_remote"))
        tool_names = {tool["name"] for tool in mcp.tool_specs()}
        self.assertIn("win_capture", tool_names)
        self.assertIn("win_wsl_resident", tool_names)

    def test_win_capture_calls_v3_client_directly(self) -> None:
        target = self.make_target()
        with mock.patch.object(mcp.cli, "load_target", return_value=target), \
             mock.patch.object(mcp.v3, "process_capture", return_value=self.make_call(target)) as process_capture:
            result = mcp.handle_tool_call(
                "win_capture",
                {
                    "target": "TEST",
                    "program": "whoami.exe",
                    "args": ["/user"],
                    "cwd": "C:/CodexRemote/inbox",
                },
            )

        process_capture.assert_called_once_with(
            target,
            "whoami.exe",
            ["/user"],
            cwd="C:/CodexRemote/inbox",
            allow_powershell=False,
        )
        self.assertFalse(result["isError"])
        payload = json.loads(result["content"][0]["text"])
        self.assertEqual(payload["transport"], "v3")
        self.assertEqual(payload["stdout"], "OK\n")
        self.assertEqual(result["structuredContent"]["id"], "req-1")

    def test_win_put_uses_v3_mkdir_then_scp(self) -> None:
        target = self.make_target()
        with tempfile.TemporaryDirectory() as tmp:
            local_path = Path(tmp) / "payload.txt"
            local_path.write_text("payload", encoding="utf-8")
            mkdir_call = mcp.v3.RpcCall(
                target=target,
                request={"id": "mkdir", "action": "file.mkdir"},
                response={"id": "mkdir", "ok": True, "exitCode": 0},
                argv=[],
                ssh_returncode=0,
                ssh_stderr="",
            )
            with mock.patch.object(mcp.cli, "load_target", return_value=target), \
                 mock.patch.object(mcp.v3, "file_mkdir", return_value=mkdir_call) as file_mkdir, \
                 mock.patch.object(mcp.cli, "scp_to_remote") as scp_to_remote:
                result = mcp.handle_tool_call(
                    "win_put",
                    {"target": "TEST", "local_path": str(local_path), "remote_path": "C:/CodexRemote/inbox/payload.txt"},
                )

        file_mkdir.assert_called_once_with(target, "C:/CodexRemote/inbox")
        scp_to_remote.assert_called_once()
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["transport"], "v3")

    def test_wsl_python_requires_module_or_script_path(self) -> None:
        target = self.make_target()
        with mock.patch.object(mcp.cli, "load_target", return_value=target):
            result = mcp.handle_tool_call("win_wsl_py", {"target": "TEST"})

        self.assertTrue(result["isError"])
        self.assertIn("exactly one", result["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
