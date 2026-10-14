"""Exercise the onboarding CLI's credential and host-selection boundaries."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import shlex
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "create-wre-target.py"
SPEC = importlib.util.spec_from_file_location("create_wre_target", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def values(path: Path) -> dict[str, str]:
    return dict(shlex.split(line)[0].split("=", 1) for line in path.read_text().splitlines())


class TargetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.key = self.root / "controller ' key"
        self.key.write_text("PRIVATE-KEY-FIXTURE-DO-NOT-TRANSFER")
        key_type = b"ssh-ed25519"
        key_data = struct.pack(">I", len(key_type)) + key_type + struct.pack(">I", 32) + bytes(32)
        Path(str(self.key) + ".pub").write_text(
            "ssh-ed25519 " + base64.b64encode(key_data).decode() + " WRE test\n"
        )
        self.directory = self.root / "private targets"
        self.target = self.directory / "TEST-HOST.env"
        self.bootstrap = self.directory / "TEST-HOST.bootstrap.json"

    def cli(self, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--name", "TEST-HOST", "--host", "100.64.0.1",
             "--controller-address", "100.64.0.2", "--user", "wreuser", "--key", str(self.key),
             "--targets-dir", str(self.directory), *extra],
            capture_output=True, text=True, timeout=15,
        )

    def test_cli_stores_secret_privately_and_transfers_only_hash_and_public_key(self):
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(self.bootstrap.read_text())
        target = values(self.target)
        secret = target["TARGET_ACCESS_TOKEN"]
        self.assertEqual(len(secret), 64)
        self.assertEqual(document["tokenSha256"], hashlib.sha256(secret.encode()).hexdigest())
        self.assertNotIn(secret, self.bootstrap.read_text())
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertNotIn(self.key.read_text(), self.bootstrap.read_text())
        self.assertEqual(target["TARGET_KEY"], str(self.key.resolve()))
        self.assertEqual(document["controllerAddress"], "100.64.0.2")
        for file in (self.target, self.bootstrap):
            self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)

    def test_existing_credentials_are_unchanged_without_explicit_reuse_or_rotation(self):
        self.assertEqual(self.cli().returncode, 0)
        original = self.target.read_bytes(), self.bootstrap.read_bytes()
        result = self.cli()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(original, (self.target.read_bytes(), self.bootstrap.read_bytes()))
        self.assertIn("--reuse", result.stderr)

    def test_reuse_keeps_exact_existing_env_and_token(self):
        self.assertEqual(self.cli().returncode, 0)
        original = self.target.read_bytes(), self.bootstrap.read_bytes()
        result = self.cli("--reuse")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(original, (self.target.read_bytes(), self.bootstrap.read_bytes()))

    def test_explicit_rotation_keeps_a_private_backup(self):
        self.assertEqual(self.cli().returncode, 0)
        original = self.target.read_bytes()
        old_secret = values(self.target)["TARGET_ACCESS_TOKEN"]
        result = self.cli("--rotate-token")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(old_secret, values(self.target)["TARGET_ACCESS_TOKEN"])
        backups = list(self.directory.glob("TEST-HOST.env.before-rotation-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)

    def test_lan_example_and_path_traversal_fail_before_output_directory_creation(self):
        for extra in (("--host", "192.168.50.20"), ("--name", "../escape")):
            with self.subTest(extra=extra):
                result = self.cli(*extra)
                self.assertEqual(result.returncode, 1)
                self.assertFalse(self.directory.exists())

    def test_private_or_multiple_keys_are_rejected_before_credential_creation(self):
        public_file = Path(str(self.key) + ".pub")
        original = public_file.read_text()
        for text in ("-----BEGIN OPENSSH PRIVATE KEY-----\n", original + original):
            with self.subTest(kind=text[:12]):
                public_file.write_text(text)
                result = self.cli()
                self.assertEqual(result.returncode, 1)
                self.assertFalse(self.directory.exists())
                self.assertNotIn(text.strip(), result.stdout + result.stderr)

    def test_offline_or_ambiguous_peer_is_not_guessed(self):
        peer = {"HostName": "win-test", "Online": False, "TailscaleIPs": ["100.64.0.1"]}
        for peers in ({"one": peer}, {"one": peer, "two": {**peer, "Online": True}}):
            result = subprocess.CompletedProcess([], 0, stdout=json.dumps({"Peer": peers}))
            with self.subTest(peers=len(peers)), patch.object(MODULE.subprocess, "run", return_value=result):
                with self.assertRaises(ValueError):
                    MODULE.resolve_device("WIN-TEST")

    def test_online_exact_peer_uses_its_ipv4(self):
        peer = {"HostName": "win-test", "DNSName": "win-test.example.ts.net.",
                "Online": True, "TailscaleIPs": ["fd7a:115c:a1e0::1", "100.64.0.1"]}
        result = subprocess.CompletedProcess([], 0, stdout=json.dumps({"Peer": {"one": peer}}))
        with patch.object(MODULE.subprocess, "run", return_value=result):
            self.assertEqual(MODULE.resolve_device("WIN-TEST"), "100.64.0.1")
            self.assertEqual(MODULE.resolve_device("win-test.example.ts.net"), "100.64.0.1")


if __name__ == "__main__":
    unittest.main()
