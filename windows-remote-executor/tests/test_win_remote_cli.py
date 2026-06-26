#!/usr/bin/env python3

from __future__ import annotations

import base64
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import win_remote_cli as cli  # noqa: E402


class WinRemoteCliTests(unittest.TestCase):
    def test_envelope_preserves_hostile_arguments(self) -> None:
        args = [
            "space arg",
            "quote \" arg",
            "tick ` dollar $ paren $(x)",
            "percent %PATH% bang !VAR! amp & pipe | lt < gt >",
            "json {\"a\":[1,2]}",
            "日本語 中文 한글",
            "line1\nline2",
        ]
        request = {"action": "process.capture", "file": "C:/Tools/echo.exe", "cwd": "D:/Work Dir", "args": args}

        envelope = cli.encode_envelope(request)
        decoded = json.loads(cli.base64_url_decode(envelope).decode("utf-8"))

        self.assertEqual(decoded, request)
        self.assertNotRegex(envelope, cli.SCRIPT_ACTIVE_CHARS)

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

    def test_release_zip_validation_requires_fdd_release_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            good = tmp_path / "windows-remote-executor-native-v1.2.3-fdd-win-x64.zip"
            with zipfile.ZipFile(good, "w") as zf:
                for name in [
                    "WindowsRemoteExecutor.Native.exe",
                    "WindowsRemoteExecutor.Native.dll",
                    "WindowsRemoteExecutor.Native.runtimeconfig.json",
                    "WindowsRemoteExecutor.Native.deps.json",
                ]:
                    zf.writestr(name, "x")
            cli.validate_release_native_zip(good)

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


if __name__ == "__main__":
    unittest.main()
