"""MCP front-end tests (v6/mcp/wre_mcp.py) with a stubbed `mcp` package —
the real FastMCP is only present in the opencode runtime. Verifies:
  - tool contract freeze (names/params; return JSON strings)
  - payload shaping per tool (structured args in, documented payload out)
  - error containment: RpcError -> {"error":"transport"}, unexpected ->
    {"error":"internal"}, never a raw traceback over the stdio transport
"""

from __future__ import annotations

import json
import sys
import types
from typing import Any, ClassVar

import pytest

# ---- stub the mcp package BEFORE importing wre_mcp ----

_TOOL_REGISTRY: dict[str, Any] = {}


class _StubServer:
    def __init__(self, name: str, instructions: str = "") -> None:
        self.name = name
        self.instructions = instructions

    def tool(self):
        def deco(fn):
            _TOOL_REGISTRY[fn.__name__] = fn
            return fn
        return deco

    def run(self, transport: str = "stdio") -> None:  # pragma: no cover
        raise NotImplementedError


def _install_mcp_stub() -> None:
    mcp_mod = types.ModuleType("mcp")
    server_mod = types.ModuleType("mcp.server")
    fastmcp_mod = types.ModuleType("mcp.server.fastmcp")
    fastmcp_mod.FastMCP = _StubServer  # type: ignore[attr-defined]
    server_mod.fastmcp = fastmcp_mod  # type: ignore[attr-defined]
    mcp_mod.server = server_mod  # type: ignore[attr-defined]
    sys.modules.setdefault("mcp", mcp_mod)
    sys.modules["mcp.server"] = server_mod
    sys.modules["mcp.server.fastmcp"] = fastmcp_mod


_install_mcp_stub()

from v6.mcp import wre_mcp

EXPECTED_TOOLS = {
    "wre_run", "wre_start", "wre_wait", "wre_status", "wre_kill",
    "wre_wsl", "wre_wsl_list", "wre_wsl_status",
    "wre_read", "wre_write", "wre_list", "wre_search",
    "wre_tasks", "wre_task_run", "wre_task_create",
    "wre_info", "wre_probe", "wre_guard", "wre_help",
    "wre_find", "wre_find_check", "wre_find_catchup",
}


def test_tool_contract_names():
    assert set(_TOOL_REGISTRY) == EXPECTED_TOOLS


def test_instructions_declared():
    assert "auto-reconnects" in wre_mcp._INSTRUCTIONS
    assert "RETRY once" in wre_mcp._INSTRUCTIONS
    assert "Structured arguments only" in wre_mcp._INSTRUCTIONS


# ---------- payload shaping ----------

@pytest.fixture()
def capture(monkeypatch):
    calls: list[tuple[str, str, dict, str | None, int | None]] = []

    def _fake(target, action, payload, entry_root=None, timeout_ms=None):
        calls.append((target, action, payload, entry_root, timeout_ms))
        return '{"captured": true}'

    monkeypatch.setattr(wre_mcp, "_call", _fake)
    return calls


def test_wre_run_shapes_payload(capture):
    out = wre_mcp.wre_run("X570", "C:/Windows/System32/cmd.exe",
                          args=["/d", "/c", "echo", "hi"], cwd="C:/work",
                          stdin_text="in", env={"A": "1"}, timeout_ms=5000,
                          capture_kb=64)
    assert json.loads(out) == {"captured": True}
    _target, action, payload, _, timeout_ms = capture[0]
    assert action == "process.run"
    assert payload == {"exe": "C:/Windows/System32/cmd.exe",
                       "args": ["/d", "/c", "echo", "hi"], "cwd": "C:/work",
                       "stdinText": "in", "env": {"A": "1"},
                       "timeoutMs": 5000, "captureKB": 64}
    assert timeout_ms == 5000 + 60_000  # client margin above server timeout


def test_wre_run_minimal_omits_optional_keys(capture):
    wre_mcp.wre_run("X570", "C:/x/y.exe")
    _, _, payload, _, timeout_ms = capture[0]
    assert payload == {"exe": "C:/x/y.exe"}
    assert timeout_ms is None  # no server timeout -> no client margin


def test_wre_start_shapes_and_uses_fixed_start_deadline(capture):
    wre_mcp.wre_start("X570", "C:/x.exe", args=["a"], timeout_ms=1000)
    _, action, payload, _, timeout_ms = capture[0]
    assert action == "process.start"
    assert payload == {"exe": "C:/x.exe", "args": ["a"], "timeoutMs": 1000}
    assert timeout_ms == 60_000  # the start handshake itself is quick


def test_wre_wait_status_kill(capture):
    wre_mcp.wre_wait("X570", "j-0123456789abcdef", timeout_ms=3000, tail_kb=8)
    assert capture[0][1] == "process.wait"
    assert capture[0][2] == {"jobId": "j-0123456789abcdef", "timeoutMs": 3000, "tailKB": 8}
    assert capture[0][4] == 3000 + 60_000
    wre_mcp.wre_status("X570", "j-0123456789abcdef")
    assert capture[1][1] == "process.status"
    wre_mcp.wre_kill("X570", "j-0123456789abcdef", force=True)
    assert capture[2][1] == "process.kill"
    assert capture[2][2] == {"jobId": "j-0123456789abcdef", "force": True}


def test_wre_wsl_shapes(capture):
    wre_mcp.wre_wsl("X570", argv=["/bin/ls"], distro="Ubuntu", user="me",
                    win_cwd="C:/work", timeout_ms=7000)
    _, action, payload, _, timeout_ms = capture[0]
    assert action == "wsl.run"
    assert payload == {"argv": ["/bin/ls"], "distro": "Ubuntu", "user": "me",
                       "winCwd": "C:/work", "timeoutMs": 7000}
    assert timeout_ms == 7000 + 60_000
    wre_mcp.wre_wsl("X570", shell="echo hi")
    assert capture[1][2] == {"shell": "echo hi"}
    assert capture[1][4] == 120_000  # default warm-up budget


def test_wre_read_write_list_search(capture):
    wre_mcp.wre_read("X570", "C:/a.log", tail=4000)
    assert capture[0][1] == "file.readText"
    assert capture[0][2] == {"path": "C:/a.log", "tail": 4000}
    wre_mcp.wre_write("X570", "C:/a.txt", "hello 雪")
    assert capture[1][1] == "file.writeText"
    assert capture[1][2] == {"path": "C:/a.txt", "text": "hello 雪"}
    wre_mcp.wre_list("X570", "C:/d", pattern="*.log", recursive=True, max_entries=10)
    assert capture[2][1] == "file.list"
    assert capture[2][2] == {"path": "C:/d", "pattern": "*.log",
                             "recursive": True, "maxEntries": 10}
    wre_mcp.wre_search("X570", "C:/d", name_glob="*.log", content_regex="err",
                       max_matches=5)
    assert capture[3][1] == "file.search"
    assert capture[3][2] == {"root": "C:/d", "maxMatches": 5,
                             "nameGlob": "*.log", "contentRegex": "err"}


def test_wre_tasks_family(capture):
    wre_mcp.wre_tasks("X570", prefix="WRE", verbose=True, limit=5, older_than_days=30)
    assert capture[0][1] == "host.tasks.list"
    assert capture[0][2] == {"verbose": True, "prefix": "WRE", "limit": 5,
                             "olderThanDays": 30}
    wre_mcp.wre_task_run("X570", "WRE WRE Apply")
    assert capture[1][1] == "host.task.run"
    assert capture[1][2] == {"name": "WRE WRE Apply"}
    wre_mcp.wre_task_create("X570", "T", "C:/x.exe", args=["a"], trigger="manual",
                            delete_after_run=True, expires_at="2026-01-01T00:00:00")
    _, action, payload, _, _ = capture[2]
    assert action == "host.task.create"
    assert payload == {"name": "T", "exe": "C:/x.exe", "trigger": "manual",
                       "run_as_user": "SYSTEM", "args": ["a"],
                       "deleteAfterRun": True, "expiresAt": "2026-01-01T00:00:00"}


def test_wre_host_tools(capture):
    wre_mcp.wre_info("X570")
    assert capture[0][1] == "host.info" and capture[0][4] == 30_000
    wre_mcp.wre_probe("X570")
    assert capture[1][1] == "host.probe"
    assert capture[1][2] == {"categories": ["os", "sshd", "policy"]}
    wre_mcp.wre_guard("X570")
    assert capture[2][1] == "host.guard" and capture[2][2] == {"noDisable": True}
    wre_mcp.wre_help("X570", action="process.run")
    assert capture[3][1] == "system.help" and capture[3][2] == {"action": "process.run"}
    wre_mcp.wre_help("X570")
    assert capture[4][2] == {}


def test_wre_find_family(capture):
    wre_mcp.wre_find("X570", "neo mkv", n=10, fresh=True, path_mode=True,
                     json_out=False, sort_name=True)
    _, action, payload, _, timeout_ms = capture[0]
    assert action == "process.run"
    assert payload["exe"] == wre_mcp._WREQ
    assert payload["args"] == ["find", "neo mkv", "-n", "10", "-fresh", "-path",
                               "-sort"]
    assert payload["timeoutMs"] == 90_000  # fresh costs more
    assert timeout_ms == 120_000
    wre_mcp.wre_find_check("X570", db="D:/idx.db")
    assert capture[1][2]["args"] == ["check", "-db", "D:/idx.db"]
    wre_mcp.wre_find_catchup("X570", max_age_min=5.5)
    assert capture[2][2]["args"] == ["catchup", "--max-age", "5.5"]
    assert capture[2][4] == 240_000


def test_entry_root_passthrough(capture):
    wre_mcp.wre_info("X570", entry_root="C:/WRE/wre6")
    assert capture[0][3] == "C:/WRE/wre6"


# ---------- error containment ----------

@pytest.fixture()
def clear_sessions():
    wre_mcp._sessions.clear()
    yield
    wre_mcp._sessions.clear()


def test_call_transport_error_structured(monkeypatch, clear_sessions, fake_target):
    class _Sess:
        def call(self, *a, **kw):
            raise wre_mcp.rpc.RpcTransportError("ssh died")

    monkeypatch.setattr(wre_mcp, "_session", lambda *a, **kw: _Sess())
    out = json.loads(wre_mcp.wre_info("X570"))
    assert out == {"error": "transport", "message": "ssh died"}


def test_call_unexpected_exception_contained(monkeypatch, clear_sessions):
    class _Sess:
        def call(self, *a, **kw):
            raise ZeroDivisionError("boom")

    monkeypatch.setattr(wre_mcp, "_session", lambda *a, **kw: _Sess())
    out = json.loads(wre_mcp.wre_info("X570"))
    assert out["error"] == "internal"
    assert "ZeroDivisionError: boom" in out["message"]
    assert "Traceback" not in json.dumps(out)


def test_call_remote_error_passthrough(monkeypatch, clear_sessions):
    class _Call:
        ok = False
        error_class = "auth"
        message = "denied"
        response: ClassVar[dict] = {"didYouMean": ""}

    class _Sess:
        def call(self, *a, **kw):
            return _Call()

    monkeypatch.setattr(wre_mcp, "_session", lambda *a, **kw: _Sess())
    out = json.loads(wre_mcp.wre_info("X570"))
    assert out["error"] == "auth" and out["message"] == "denied"


def test_call_success_returns_data_json(monkeypatch, clear_sessions):
    class _Call:
        ok = True
        data: ClassVar[dict] = {"uptimeHours": 1.5}

    class _Sess:
        def call(self, *a, **kw):
            return _Call()

    monkeypatch.setattr(wre_mcp, "_session", lambda *a, **kw: _Sess())
    assert json.loads(wre_mcp.wre_info("X570")) == {"uptimeHours": 1.5}


def test_unknown_target_error_is_structured(clear_sessions):
    out = json.loads(wre_mcp.wre_info("no-such-target-zzz"))
    assert out["error"] == "transport"
    assert "cannot load target" in out["message"]


def test_session_open_failure_is_transport_not_crash(monkeypatch, clear_sessions, fake_target):
    monkeypatch.setattr(wre_mcp.tgt, "load_target", lambda name: fake_target)

    class _BadSession:
        def __init__(self, *a, **kw):
            pass

        def _open(self):
            raise OSError("ssh binary missing")

    monkeypatch.setattr(wre_mcp.rpc, "Session", _BadSession)
    out = json.loads(wre_mcp.wre_info("X570"))
    assert out["error"] == "transport"
    assert "Traceback" not in json.dumps(out)
    # the failed session must NOT be cached — a later retry gets a fresh one
    assert wre_mcp._sessions == {}


def test_session_cache_keyed_by_target_and_entry(monkeypatch, clear_sessions, fake_target):
    monkeypatch.setattr(wre_mcp.tgt, "load_target", lambda name: fake_target)
    opened = []

    class _Sess:
        def __init__(self, target, entry_root=None):
            self.entry_root = entry_root

        def _open(self):
            opened.append(self)

    monkeypatch.setattr(wre_mcp.rpc, "Session", _Sess)
    wre_mcp._session("X570", None)
    wre_mcp._session("X570", None)          # cached
    wre_mcp._session("X570", "C:/WRE/wre6")  # different entry -> new session
    assert len(opened) == 2
    assert ("X570", "C:/WRE/wre") in wre_mcp._sessions
    assert ("X570", "C:/WRE/wre6") in wre_mcp._sessions
