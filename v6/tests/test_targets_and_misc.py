"""Coverage-fill tests for controller targets, native maintenance plumbing,
wsl actions via a mock WRE_WSL_EXE, and the shell CLI helpers — still zero
network: every transport boundary is mocked or local."""

from __future__ import annotations

import io
import json
import os
import stat
from typing import ClassVar

import pytest
import rpc as native_rpc

from v6.controller import client as rpc
from v6.controller import targets as tgt

# ---------- targets (.env parsing + the documented v3-tree coupling) ----------

def test_parse_env_file_full(tmp_path):
    env = tmp_path / "X.env"
    env.write_text(
        "# comment\n"
        "TARGET_NAME=X570\n"
        "export TARGET_HOST=100.0.0.1\n"
        'TARGET_USER="sumie san"\n'          # quoted value with space
        "TARGET_PORT=2222\n"
        "BROKEN LINE WITHOUT EQUALS\n"
        "=orphan\n"
        "TARGET_KEY=~/.ssh/id_ed25519\n"
        "INLINE=1 # comment makes this line skipped entirely\n"
        "unterminated='quote\n",             # ValueError -> skipped
    )
    values = tgt.parse_env_file(env)
    assert values["TARGET_NAME"] == "X570"
    assert values["TARGET_HOST"] == "100.0.0.1"
    assert values["TARGET_USER"] == "sumie san"
    assert values["TARGET_PORT"] == "2222"
    assert values["TARGET_KEY"] == "~/.ssh/id_ed25519"
    assert "BROKEN" not in values and "" not in values
    assert "unterminated" not in values
    assert "INLINE" not in values  # shlex keeps '#' -> multi-token -> skipped


def test_load_target_from_env_file(tmp_path):
    env = tmp_path / "T.env"
    env.write_text("TARGET_HOST=10.0.0.2\nTARGET_USER=u\n")
    t = tgt.load_target(str(env))
    assert t.host == "10.0.0.2" and t.user == "u" and t.port == "22"
    assert t.exposure_mode == "private-only"
    assert t.ssh_destination == "u@10.0.0.2"
    assert t.ssh_args[:2] == ["ssh", "-p"]
    assert "BatchMode=yes" in t.ssh_args
    assert "-i" not in t.ssh_args  # no key configured


def test_load_target_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        tgt.load_target(str(tmp_path / "nope"))


def test_load_target_requires_host_and_user(tmp_path):
    env = tmp_path / "T.env"
    env.write_text("TARGET_USER=u\n")
    with pytest.raises(RuntimeError, match="TARGET_HOST is required"):
        tgt.load_target(str(env))
    env.write_text("TARGET_HOST=h\n")
    with pytest.raises(RuntimeError, match="TARGET_USER is required"):
        tgt.load_target(str(env))


def test_targets_dir_resolution_chain():
    # De-parasitized 2026-09-16 (QC-WRE DEBT): chain = $WRE_TARGETS_DIR →
    # v6/targets/ → legacy v3 dir (one-release compat). The .env files now
    # live in v6/targets/, so the default resolves there.
    assert tgt.TARGETS_DIR == tgt.V6_TARGETS_DIR
    assert tgt.TARGETS_DIR.parent.name == "v6"
    assert (tgt.TARGETS_DIR / "X570.env").is_file()


def test_targets_dir_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("WRE_TARGETS_DIR", str(tmp_path))
    assert tgt._resolve_targets_dir() == tmp_path


def test_targets_dir_legacy_v3_fallback(monkeypatch):
    monkeypatch.delenv("WRE_TARGETS_DIR", raising=False)
    monkeypatch.setattr(tgt, "V6_TARGETS_DIR", tgt.V6_TARGETS_DIR.parent / "no-such-dir")
    assert tgt._resolve_targets_dir() == tgt.LEGACY_V3_TARGETS_DIR
    assert tgt.LEGACY_V3_TARGETS_DIR.parent.name == "windows-remote-executor"


def test_list_targets(monkeypatch, tmp_path):
    (tmp_path / "X570.env").write_text("TARGET_HOST=h\nTARGET_USER=u\n")
    (tmp_path / "nuc-8-sumie.env").write_text("TARGET_HOST=h\nTARGET_USER=u\n")
    (tmp_path / "example.env").write_text("")
    monkeypatch.setattr(tgt, "TARGETS_DIR", tmp_path)
    assert tgt.list_targets() == ["X570", "nuc-8-sumie"]
    monkeypatch.setattr(tgt, "TARGETS_DIR", tmp_path / "gone")
    assert tgt.list_targets() == []


# ---------- check_peer_version ----------

def test_check_peer_version_v5_hints(monkeypatch, fake_target, capsys):
    class _C:
        data: ClassVar[dict] = {"version": 5}
        response: ClassVar[dict] = {"protocolVersion": 5}

    monkeypatch.setattr(rpc, "call", lambda *a, **kw: _C())
    assert rpc.check_peer_version(fake_target) == 5
    assert "protocol v5" in capsys.readouterr().err


def test_check_peer_version_v6_silent_and_error_zero(monkeypatch, fake_target, capsys):
    class _C:
        data: ClassVar[dict] = {"version": 6}
        response: ClassVar[dict] = {"protocolVersion": 6}

    monkeypatch.setattr(rpc, "call", lambda *a, **kw: _C())
    assert rpc.check_peer_version(fake_target) == 6
    assert capsys.readouterr().err == ""

    def _boom(*a, **kw):
        raise rpc.RpcTransportError("down")

    monkeypatch.setattr(rpc, "call", _boom)
    assert rpc.check_peer_version(fake_target) == 0


# ---------- native maintenance plumbing ----------

def test_parse_kv_flags():
    flags = native_rpc._parse_kv_flags(["--spec", "/tmp/x.json", "--force", "--log-path", "y"])
    assert flags == {"spec": "/tmp/x.json", "force": "1", "log-path": "y"}
    assert native_rpc._parse_kv_flags([]) == {}
    assert native_rpc._parse_kv_flags(["positional", "--a", "--b", "2"]) == {"a": "1", "b": "2"}


def test_main_unknown_subcommand(capsys):
    assert native_rpc.main.__wrapped__ if hasattr(native_rpc.main, "__wrapped__") else True
    import sys
    old = sys.argv
    try:
        sys.argv = ["rpc.py", "bogus-subcommand"]
        assert native_rpc.main() == 2
        assert "unknown subcommand" in capsys.readouterr().err
        sys.argv = ["rpc.py", "selftest"]
        assert native_rpc.main() == 0
        out = capsys.readouterr().out
        assert json.loads(out)["ok"] is True
    finally:
        sys.argv = old


def test_rpc_loop_in_process(monkeypatch):
    """Drive _rpc_loop with in-memory stdio: structured errors for malformed
    lines, clean exit at EOF."""
    stdin = io.StringIO('{"action":"host.capabilities","id":"in-proc"}\n'
                        "not json\n"
                        "\n")
    stdout = io.StringIO()
    monkeypatch.setattr(native_rpc.sys, "stdin", stdin)
    monkeypatch.setattr(native_rpc.sys, "stdout", stdout)
    assert native_rpc._rpc_loop() == 0
    lines = [json.loads(ln) for ln in stdout.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 2
    assert lines[0]["ok"] is True and lines[0]["id"] == "in-proc"
    assert lines[1]["ok"] is False and lines[1]["errorClass"] == "protocol"


# ---------- wsl actions via mock exe ----------

@pytest.fixture()
def mock_wsl_exe(tmp_path, monkeypatch):
    from actions import wsl
    script = tmp_path / "mock-wsl.sh"
    script.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-l" ]; then\n'
        '  printf "  NAME           STATE      VERSION\\n"\n'
        '  printf "* Ubuntu-22.04   Running    2\\n"\n'
        '  printf "  Debian         Stopped    2\\n"\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = "--exec" ] && [ "$2" = "wslpath" ]; then\n'
        "  echo /home/me/work\n"
        "  exit 0\n"
        "fi\n"
        "echo mock-wsl: \"$@\"\n",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("WRE_WSL_EXE", str(script))
    return wsl


def test_wsl_list_via_mock(mock_wsl_exe):
    out = mock_wsl_exe.wsl_list({})
    distros = out["data"]["distros"]
    assert distros[0]["name"] == "Ubuntu-22.04" and distros[0]["default"] is True
    assert distros[1]["state"] == "Stopped"


def test_wsl_status_via_mock(mock_wsl_exe):
    out = mock_wsl_exe.wsl_status({})
    assert out["data"]["available"] is True
    assert out["data"]["vmRunning"] is True
    assert out["data"]["defaultDistro"] == "Ubuntu-22.04"


def test_wsl_status_unavailable(monkeypatch):
    from actions import wsl
    monkeypatch.delenv("WRE_WSL_EXE", raising=False)
    if os.name != "nt":
        out = wsl.wsl_status({})
        assert out["data"]["available"] is False


def test_wsl_wslpath_via_mock(mock_wsl_exe):
    out = mock_wsl_exe.wsl_wslpath({"path": "C:\\work", "to": "u"})
    assert out["data"]["path"] == "/home/me/work"
    assert out["data"]["argv" if "argv" in out["data"] else "input"] == "C:\\work"


def test_wsl_wslpath_validation(mock_wsl_exe):
    with pytest.raises(ValueError, match="requires payload.path"):
        mock_wsl_exe.wsl_wslpath({"to": "u"})
    with pytest.raises(ValueError, match="'u'.*'w'"):
        mock_wsl_exe.wsl_wslpath({"path": "C:/x", "to": "sideways"})


# ---------- files: WSL-mediated write (mock transport) ----------

def test_wsl_mediated_write_verified(monkeypatch):
    import hashlib

    from actions import files
    data = b"payload-bytes"
    digest = hashlib.sha256(data).hexdigest()
    calls = []

    def _capture(argv, stdin_text):
        calls.append(argv)
        if "sha256sum" in argv[-1] or "/usr/bin/sha256sum" in argv:
            return {"exitCode": 0, "stdout": f"{digest}  -\n", "stderr": ""}
        return {"exitCode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(files, "_wsl_run_capture", _capture)
    proof = files._wsl_mediated_write("Ubuntu", "/home/me/f.bin", data)
    assert proof["sha256"] == digest
    assert proof["verifiedAgainst"] == "sha256sum on target"
    assert proof["transport"] == "wsl-stdin"
    # the path travels as its own argv element ($1 positional), never interpolated
    write_call = next(a for a in calls if "bash" in " ".join(a[:6]))
    assert "$1" in " ".join(write_call)


def test_wsl_mediated_write_hash_mismatch_raises(monkeypatch):
    from actions import files

    def _capture(argv, stdin_text):
        if "/usr/bin/sha256sum" in argv:
            return {"exitCode": 0, "stdout": "deadbeef  -\n", "stderr": ""}
        return {"exitCode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(files, "_wsl_run_capture", _capture)
    with pytest.raises(RuntimeError, match="verification failed"):
        files._wsl_mediated_write("Ubuntu", "/home/me/f.bin", b"data")


def test_wsl_mediated_write_mkdir_failure_raises(monkeypatch):
    from actions import files

    def _capture(argv, stdin_text):
        return {"exitCode": 1, "stdout": "", "stderr": "denied"}

    monkeypatch.setattr(files, "_wsl_run_capture", _capture)
    with pytest.raises(RuntimeError, match="mkdir -p failed"):
        files._wsl_mediated_write("Ubuntu", "/home/me/f.bin", b"data")


def test_write_or_hint_escalates_to_wsl_mediation(monkeypatch, tmp_path):
    from actions import files

    def _boom_write(path, data):
        raise OSError("WinError 67")

    def _mediated(distro, posix_path, data):
        assert distro == "Ubuntu" and posix_path == "/home/me/f.txt"
        return {"path": "//wsl.localhost/Ubuntu/home/me/f.txt", "sha256": "x",
                "transport": "wsl-stdin"}

    monkeypatch.setattr(files, "_write_bytes", _boom_write)
    monkeypatch.setattr(files, "_wsl_mediated_write", _mediated)
    proof = files._write_or_hint("//wsl.localhost/Ubuntu/home/me/f.txt",
                                 "/unc/f.txt", b"data")
    assert proof["transport"] == "wsl-stdin"

    def _mediated_fail(distro, posix_path, data):
        raise RuntimeError("no distro")

    monkeypatch.setattr(files, "_wsl_mediated_write", _mediated_fail)
    with pytest.raises(ValueError, match="UNC failed.*WSL-mediated"):
        files._write_or_hint("//wsl.localhost/Ubuntu/home/me/f.txt",
                             "/unc/f.txt", b"data")
    # non-UNC paths re-raise the plain OSError, no mediation
    with pytest.raises(OSError, match="WinError 67"):
        files._write_or_hint("C:/plain/f.txt", "C:/plain/f.txt", b"data")


# ---------- shell CLI helpers ----------

def test_shell_load_payload(tmp_path):
    from v6.controller import shell
    assert shell._load_payload(None) == {}
    assert shell._load_payload('{"a": 1}') == {"a": 1}
    f = tmp_path / "p.json"
    f.write_text('{"b": 2}')
    assert shell._load_payload(f"@{f}") == {"b": 2}
    with pytest.raises(json.JSONDecodeError):
        shell._load_payload("{bad")


def test_shell_main_info(monkeypatch, fake_target, capsys):
    from v6.controller import shell
    monkeypatch.setattr(shell.tgt, "load_target", lambda name: fake_target)

    class _C:
        ok = True
        data: ClassVar[dict] = {}
        response: ClassVar[dict] = {"ok": True, "protocolVersion": 6, "data": {"build": "v6.1"}}
        stdout_text = ""

    monkeypatch.setattr(shell.rpc, "call", lambda *a, **kw: _C())
    rc = shell.main(["X570", "--info"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True


def test_shell_main_failed_call_returns_1(monkeypatch, fake_target, capsys):
    from v6.controller import shell
    monkeypatch.setattr(shell.tgt, "load_target", lambda name: fake_target)

    class _C:
        ok = False
        response: ClassVar[dict] = {"ok": False, "errorClass": "auth", "message": "denied"}
        stdout_text = ""

    monkeypatch.setattr(shell.rpc, "call", lambda *a, **kw: _C())
    monkeypatch.setattr(shell.rpc, "check_peer_version", lambda *a, **kw: 6)
    assert shell.main(["X570", "process.run", '{"exe":"C:/x"}']) == 1
