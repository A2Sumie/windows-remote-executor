#!/usr/bin/env python3

from __future__ import annotations

import base64
import io
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import win_remote_cli as cli  # noqa: E402


class WinRemoteCliTests(unittest.TestCase):
    def test_normalize_remote_path_rejects_drive_relative(self) -> None:
        with self.assertRaises(cli.WinRemoteError):
            cli.normalize_remote_path(r"D:StreamServfile.py")

        self.assertEqual(cli.normalize_remote_path(r"D:\StreamServ\file.py"), "D:/StreamServ/file.py")

    def test_remote_parent_handles_windows_paths(self) -> None:
        self.assertEqual(cli.remote_parent("C:/CodexRemote/tools/WindowsRemoteExecutor.Native.exe"), "C:/CodexRemote/tools")

    def test_target_env_parser_handles_shell_quotes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / "x.env"
            env_path.write_text(
                "\n".join(
                    [
                        "TARGET_NAME=X",
                        "TARGET_HOST=100.64.1.2",
                        "TARGET_USER='User Name'",
                        "TARGET_ACCESS_TOKEN='tok en'",
                        "TARGET_NATIVE_EXE='C:/Path With Spaces/WindowsRemoteExecutor.Native.exe'",
                    ]
                ),
                encoding="utf-8",
            )

            values = cli.parse_env_file(env_path)

        self.assertEqual(values["TARGET_USER"], "User Name")
        self.assertEqual(values["TARGET_ACCESS_TOKEN"], "tok en")
        self.assertEqual(values["TARGET_NATIVE_EXE"], "C:/Path With Spaces/WindowsRemoteExecutor.Native.exe")

    def test_access_token_is_standard_base64_for_existing_native_parser(self) -> None:
        encoded = cli.b64_utf8("tok en")
        self.assertEqual(base64.b64decode(encoded).decode("utf-8"), "tok en")

    def test_release_zip_validation_accepts_scd_and_fdd_release_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            scd = tmp_path / "windows-remote-executor-native-v1.2.3-scd-win-x64.zip"
            with zipfile.ZipFile(scd, "w") as zf:
                zf.writestr("WindowsRemoteExecutor.Native.exe", "x")
            self.assertEqual(cli.validate_release_native_zip(scd), "scd")

            fdd = tmp_path / "windows-remote-executor-native-v1.2.3-fdd-win-x64.zip"
            with zipfile.ZipFile(fdd, "w") as zf:
                for name in [
                    "WindowsRemoteExecutor.Native.exe",
                    "WindowsRemoteExecutor.Native.dll",
                    "WindowsRemoteExecutor.Native.runtimeconfig.json",
                    "WindowsRemoteExecutor.Native.deps.json",
                ]:
                    zf.writestr(name, "x")
            self.assertEqual(cli.validate_release_native_zip(fdd), "fdd")

            wrong_name = tmp_path / "dev-build.zip"
            with zipfile.ZipFile(wrong_name, "w") as zf:
                zf.writestr("WindowsRemoteExecutor.Native.exe", "x")
            with self.assertRaises(cli.WinRemoteError):
                cli.validate_release_native_zip(wrong_name)

            missing = tmp_path / "windows-remote-executor-native-v1.2.4-fdd-win-x64.zip"
            with zipfile.ZipFile(missing, "w") as zf:
                zf.writestr("WindowsRemoteExecutor.Native.exe", "x")
            with self.assertRaises(cli.WinRemoteError):
                cli.validate_release_native_zip(missing)

    def test_wsl_container_options_can_be_interleaved_with_wsl_options(self) -> None:
        target = cli.Target(name="T", env_file=Path("t.env"), host="100.64.1.2", user="Administrator")
        argv = [
            "T",
            "--distro", "Ubuntu",
            "--container", "app one",
            "--cwd", "/tmp/work",
            "--container-cwd", "/srv/app",
            "--shell", "/bin/bash",
            "--container-user", "1000:1000",
            "--stdin",
            "--",
            "alpha beta",
        ]

        with mock.patch.object(cli, "load_target", return_value=target), \
             mock.patch.object(sys, "stdin", io.StringIO("printf '%s\\n' \"$1\"\n")):
            parsed = cli.parse_wsl_container_script(argv, capture=True)

        parsed_target, script, script_args, cwd, distro, user, shell, out_path, container = parsed
        self.assertIs(parsed_target, target)
        self.assertEqual(script, "printf '%s\\n' \"$1\"\n")
        self.assertEqual(script_args, ["alpha beta"])
        self.assertEqual(cwd, "/tmp/work")
        self.assertEqual(distro, "Ubuntu")
        self.assertIsNone(user)
        self.assertEqual(shell, "/bin/bash")
        self.assertIsNone(out_path)
        self.assertEqual(container["name"], "app one")
        self.assertEqual(container["cwd"], "/srv/app")
        self.assertEqual(container["user"], "1000:1000")

    def test_update_tools_mkdir_uses_file_mkdir_when_supported(self) -> None:
        target = cli.Target(name="T", env_file=Path("t.env"), host="100.64.1.2", user="Administrator")
        ok_call = mock.Mock(ok=True)
        client = mock.Mock()
        client.file_mkdir.return_value = ok_call

        with mock.patch.object(cli, "v3", return_value=client):
            cli.ensure_remote_dir_for_update(target, "C:/CodexRemote/tools/releases/v3-test")

        client.file_mkdir.assert_called_once_with(target, "C:/CodexRemote/tools/releases/v3-test")
        client.script_capture.assert_not_called()

    def test_update_tools_mkdir_falls_back_for_old_v3_subset(self) -> None:
        target = cli.Target(name="T", env_file=Path("t.env"), host="100.64.1.2", user="Administrator")
        mkdir_call = mock.Mock(ok=False, response={"errorClass": "unsupported"}, ssh_stderr="", exit_code=2)
        fallback_call = mock.Mock(ok=True)
        client = mock.Mock()
        client.file_mkdir.return_value = mkdir_call
        client.script_capture.return_value = fallback_call

        with mock.patch.object(cli, "v3", return_value=client):
            cli.ensure_remote_dir_for_update(target, "C:/CodexRemote/tools/releases/quote ' dir")

        client.file_mkdir.assert_called_once_with(target, "C:/CodexRemote/tools/releases/quote ' dir")
        fallback_script = client.script_capture.call_args.args[1]
        self.assertIn("[System.IO.Directory]::CreateDirectory", fallback_script)
        self.assertIn("'C:/CodexRemote/tools/releases/quote '' dir'", fallback_script)

    def test_update_tools_mkdir_does_not_hide_other_failures(self) -> None:
        target = cli.Target(name="T", env_file=Path("t.env"), host="100.64.1.2", user="Administrator")
        mkdir_call = mock.Mock(ok=False, response={"errorClass": "auth", "stderrText": "no token"}, ssh_stderr="", exit_code=3)
        client = mock.Mock()
        client.file_mkdir.return_value = mkdir_call

        with mock.patch.object(cli, "v3", return_value=client):
            with self.assertRaisesRegex(cli.WinRemoteError, "no token"):
                cli.ensure_remote_dir_for_update(target, "C:/CodexRemote/tools/releases/v3-test")

        client.script_capture.assert_not_called()

    def test_scp_remote_path_is_one_argv_token(self) -> None:
        target = cli.Target(name="T", env_file=Path("t.env"), host="100.64.1.2", user="Administrator")
        with tempfile.TemporaryDirectory() as tmp:
            local_path = Path(tmp) / "payload.txt"
            local_path.write_text("x", encoding="utf-8")
            completed = subprocess.CompletedProcess(["scp"], 0)
            remote_path = "C:/CodexRemote/inbox/literal `tick` (semi; amp&) bang! caret^.txt"

            with mock.patch.object(cli.subprocess, "run", return_value=completed) as run:
                cli.scp_to_remote(target, local_path, remote_path)

        argv = run.call_args.args[0]
        self.assertEqual(argv[-1], f"{target.user}@{target.host}:{remote_path}")


if __name__ == "__main__":
    unittest.main()
