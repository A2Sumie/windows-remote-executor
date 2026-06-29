#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import win_remote_cli as cli  # noqa: E402
import wre_v3_client as v3  # noqa: E402


class WreV3ClientTests(unittest.TestCase):
    def make_target(self) -> cli.Target:
        return cli.Target(
            name="TESTV3",
            env_file=Path("test.env"),
            host="100.64.1.2",
            user="Administrator",
            key="/tmp/test key",
            access_token="tok en",
        )

    def test_request_line_preserves_hostile_payload(self) -> None:
        args = [
            "space arg",
            "quote \" arg",
            "tick ` dollar $ paren $(x)",
            "percent %PATH% bang !VAR! amp & pipe | lt < gt >",
            "json {\"a\":[1,2]}",
            "日本語 中文 한글",
            "line1\nline2",
        ]
        request = v3.build_rpc_request(
            "process.capture",
            {"file": "C:/Tools/echo.exe", "cwd": "D:/Work Dir", "args": args},
            request_id="req-1",
            access_token="tok en",
        )

        line = v3.request_json_line(request)
        decoded = json.loads(line)

        self.assertEqual(decoded, request)
        self.assertTrue(line.endswith("\n"))
        self.assertEqual(line.count("\n"), 1)

    def test_call_rpc_keeps_payload_out_of_ssh_argv(self) -> None:
        target = self.make_target()
        hostile = "tick ` dollar $ paren $(x) amp & pipe | lt < gt >"
        request = v3.build_rpc_request(
            "process.capture",
            {"file": "C:/Tools/echo.exe", "args": [hostile]},
            request_id="req-2",
            access_token=target.access_token,
        )
        stdout = json.dumps({"id": "req-2", "ok": True, "exitCode": 0, "stdoutText": "OK"}) + "\n"
        completed = subprocess.CompletedProcess(["ssh"], 0, stdout=stdout, stderr="")

        with mock.patch.object(v3.cli, "resolve_native_path", return_value="C:/CodexRemote/tools/WindowsRemoteExecutor.Native.exe"), \
             mock.patch.object(v3.subprocess, "run", return_value=completed) as run:
            call = v3.call_rpc_request(target, request, check_support=False)

        kwargs = run.call_args.kwargs
        argv = run.call_args.args[0]
        argv_text = "\n".join(argv)

        self.assertTrue(call.ok)
        self.assertIn("rpc-stdio", argv[-1])
        self.assertNotIn(hostile, argv_text)
        self.assertIn(hostile, kwargs["input"])
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(argv[-2], target.ssh_destination)

    def test_target_supports_rpc_checks_help_once(self) -> None:
        target = self.make_target()
        os.environ.pop("_WIN_REMOTE_SUPPORTS_RPC_TESTV3", None)
        completed = subprocess.CompletedProcess(["ssh"], 0, stdout="Usage:\n  rpc-stdio\n", stderr="")

        with mock.patch.object(v3.cli, "run_remote_native", return_value=completed) as run:
            self.assertTrue(v3.target_supports_rpc(target))
            self.assertTrue(v3.target_supports_rpc(target))

        self.assertEqual(run.call_count, 1)
        os.environ.pop("_WIN_REMOTE_SUPPORTS_RPC_TESTV3", None)

    def test_process_capture_rejects_drive_relative_paths(self) -> None:
        target = self.make_target()
        with self.assertRaises(cli.WinRemoteError):
            v3.process_capture(target, r"D:Tools\echo.exe")

    def test_process_capture_blocks_raw_powershell_by_default(self) -> None:
        target = self.make_target()
        with self.assertRaisesRegex(cli.WinRemoteError, "blocks raw PowerShell transport"):
            v3.process_capture(target, "powershell.exe", ["-NoProfile", "-Command", "Get-Date"])

    def test_process_capture_allows_raw_powershell_escape_hatch(self) -> None:
        target = self.make_target()
        stdout = json.dumps({"id": "req-ps", "ok": True, "exitCode": 0, "stdoutText": "OK"}) + "\n"
        completed = subprocess.CompletedProcess(["ssh"], 0, stdout=stdout, stderr="")

        with mock.patch.object(v3, "target_supports_rpc", return_value=True), \
             mock.patch.object(v3.cli, "resolve_native_path", return_value="C:/CodexRemote/tools/WindowsRemoteExecutor.Native.exe"), \
             mock.patch.object(v3.subprocess, "run", return_value=completed):
            call = v3.process_capture(
                target,
                "pwsh",
                ["-NoProfile", "-Command", "Get-Date"],
                allow_powershell=True,
            )

        self.assertTrue(call.ok)
        self.assertEqual(call.request["payload"]["file"], "pwsh")

    def test_process_capture_honors_raw_powershell_env_escape_hatch(self) -> None:
        target = self.make_target()
        stdout = json.dumps({"id": "req-ps-env", "ok": True, "exitCode": 0, "stdoutText": "OK"}) + "\n"
        completed = subprocess.CompletedProcess(["ssh"], 0, stdout=stdout, stderr="")

        with mock.patch.dict(os.environ, {"WIN_REMOTE_ALLOW_RAW_POWERSHELL": "1"}), \
             mock.patch.object(v3, "target_supports_rpc", return_value=True), \
             mock.patch.object(v3.cli, "resolve_native_path", return_value="C:/CodexRemote/tools/WindowsRemoteExecutor.Native.exe"), \
             mock.patch.object(v3.subprocess, "run", return_value=completed):
            call = v3.process_capture(target, "C:/Program Files/PowerShell/7/pwsh.exe")

        self.assertTrue(call.ok)
        self.assertEqual(call.request["payload"]["file"], "C:/Program Files/PowerShell/7/pwsh.exe")

    def test_parse_rpc_response_uses_last_json_line(self) -> None:
        response = v3.parse_rpc_response('noise\n{"id":"req-3","ok":true,"exitCode":0}\n', "", 0)
        self.assertEqual(response["id"], "req-3")
        self.assertTrue(response["ok"])


if __name__ == "__main__":
    unittest.main()
