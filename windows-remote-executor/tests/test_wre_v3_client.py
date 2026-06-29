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

    def test_expanded_v3_action_payloads_are_structured(self) -> None:
        target = self.make_target()
        stdout = json.dumps({"id": "req-actions", "ok": True, "exitCode": 0}) + "\n"
        completed = subprocess.CompletedProcess(["ssh"], 0, stdout=stdout, stderr="")

        with mock.patch.object(v3, "target_supports_rpc", return_value=True), \
             mock.patch.object(v3.cli, "resolve_native_path", return_value="C:/CodexRemote/tools/WindowsRemoteExecutor.Native.exe"), \
             mock.patch.object(v3.subprocess, "run", return_value=completed):
            calls = [
                v3.host_guard(target, expected_listen_address="100.64.1.2", log_path="C:/CodexRemote/logs/guard.log", no_disable=True),
                v3.host_repair(target, codex_root="C:/CodexRemote", force_rewrite=True),
                v3.host_tasks(target, task_names=["CodexRemote Sshd Repair Watch"], prefix="Codex"),
                v3.host_policy(target, command_mode="argv-only", token="tok en"),
                v3.process_spawn(target, "C:/Tools/app.exe", ["alpha beta"], stdout="C:/Logs/out.txt"),
                v3.script_run(target, "echo hi", kind="cmd", cwd="C:/Work Dir"),
                v3.python_run(target, "C:/Scripts/a.py", ["x"], conda_env="base"),
                v3.wsl_script_capture(target, "printf '%s\\n' \"$1\"", ["x y"], cwd="/tmp", distribution="Ubuntu", user="sumie", shell="/bin/bash"),
                v3.wsl_resident(target, "python3 -m http.server", port=8000, health_url="http://127.0.0.1:8000/"),
                v3.file_mkdir(target, "C:/CodexRemote/inbox/new dir"),
                v3.file_copy(target, "C:/CodexRemote/inbox/a.txt", "C:/CodexRemote/inbox/b.txt"),
                v3.everything_search(target, "*.sln", max_results=5),
            ]

        actions = [call.request["action"] for call in calls]
        self.assertEqual(
            actions,
            [
                "host.guard",
                "host.repair",
                "host.tasks",
                "host.policy",
                "process.spawn",
                "script.run",
                "python.run",
                "wsl.script.capture",
                "wsl.resident",
                "file.mkdir",
                "file.copy",
                "everything.search",
            ],
        )
        self.assertEqual(calls[0].request["payload"]["logPath"], "C:/CodexRemote/logs/guard.log")
        self.assertEqual(calls[3].request["payload"]["token"], "tok en")
        self.assertEqual(calls[4].request["payload"]["args"], ["alpha beta"])
        self.assertEqual(calls[7].request["payload"]["shell"], "/bin/bash")
        self.assertIn("script", calls[8].request["payload"])
        self.assertEqual(calls[11].request["payload"], {"query": "*.sln", "max": 5})

    def test_parse_rpc_response_uses_last_json_line(self) -> None:
        response = v3.parse_rpc_response('noise\n{"id":"req-3","ok":true,"exitCode":0}\n', "", 0)
        self.assertEqual(response["id"], "req-3")
        self.assertTrue(response["ok"])


if __name__ == "__main__":
    unittest.main()
