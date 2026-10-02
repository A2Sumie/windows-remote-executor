"""D10 chaos cell: an exception underneath the MCP front-end must be contained —
`{"error": "transport"/"internal"}` JSON, never a raw traceback, and the stdio
transport must stay alive for the NEXT call.

Import-order note: the `mcp` package is stubbed (the real FastMCP only exists
inside the opencode runtime). wre_mcp is imported LAZILY inside a fixture, so
whichever test module imports it first (this one or v6/tests/test_mcp_surface.py)
binds it to its own stub — the other simply reuses the cached module. Importing
eagerly here would poison test_mcp_surface's tool registry.
"""

from __future__ import annotations

import json
import sys
import types
from typing import Any

import pytest

from v6.controller import client as rpc

pytestmark = pytest.mark.timeout(60)


def _install_mcp_stub() -> None:
    if "mcp.server.fastmcp" in sys.modules:
        return  # another module's stub (or the real package) is already bound

    class _StubServer:
        def __init__(self, name: str, instructions: str = "") -> None:
            self.name = name
            self.instructions = instructions

        def tool(self):
            def deco(fn):
                return fn
            return deco

        def run(self, transport: str = "stdio") -> None:  # pragma: no cover
            raise NotImplementedError

    mcp_mod = types.ModuleType("mcp")
    server_mod = types.ModuleType("mcp.server")
    fastmcp_mod = types.ModuleType("mcp.server.fastmcp")
    fastmcp_mod.FastMCP = _StubServer  # type: ignore[attr-defined]
    server_mod.fastmcp = fastmcp_mod  # type: ignore[attr-defined]
    mcp_mod.server = server_mod  # type: ignore[attr-defined]
    sys.modules.setdefault("mcp", mcp_mod)
    sys.modules["mcp.server"] = server_mod
    sys.modules["mcp.server.fastmcp"] = fastmcp_mod


@pytest.fixture()
def wre_mcp():
    _install_mcp_stub()
    from v6.mcp import wre_mcp as mod
    return mod


def _result_shape(out: str) -> dict:
    resp = json.loads(out)  # must be parseable JSON — never a traceback blob
    assert "Traceback (most recent call last)" not in out
    return resp


class TestMcpContainment:
    def test_transport_error_shape_then_stdio_survives(self, wre_mcp, monkeypatch, fake_target):
        def _broken_session(target_name, entry_root):
            raise rpc.RpcTransportError("ssh session broke: tailscale flap")
        monkeypatch.setattr(wre_mcp, "_session", _broken_session)
        resp = _result_shape(wre_mcp.wre_info("TEST"))
        assert resp == {"error": "transport",
                        "message": "ssh session broke: tailscale flap"}

        # fault lifts: the SAME stdio server answers normally (E6 at the MCP面)
        class _OkSession:
            def call(self, action, payload, timeout_ms=None):
                return rpc.Call(target=fake_target, request={"action": action},
                                response={"ok": True, "data": {"who": "TEST"}},
                                argv=[], returncode=0)
        monkeypatch.setattr(wre_mcp, "_session",
                            lambda target_name, entry_root: _OkSession())
        resp = _result_shape(wre_mcp.wre_info("TEST"))
        assert resp == {"who": "TEST"}

    def test_internal_blowup_is_contained(self, wre_mcp, monkeypatch):
        """An unexpected exception (bug underneath) must surface as
        {"error":"internal"} — the stdio transport must never die."""
        def _exploding_session(target_name, entry_root):
            raise RuntimeError("boom: null pointer in the depths")
        monkeypatch.setattr(wre_mcp, "_session", _exploding_session)
        resp = _result_shape(wre_mcp.wre_read("TEST", "C:/x"))
        assert resp["error"] == "internal"
        assert "RuntimeError: boom" in resp["message"]
        # and the server is still alive for the next tool call
        resp = _result_shape(wre_mcp.wre_info("TEST"))
        assert resp["error"] == "internal"

    def test_protocol_error_maps_to_structured_error(self, wre_mcp, monkeypatch):
        def _protocol_broken(target_name, entry_root):
            raise rpc.RpcProtocolError("rpc-stdio did not return JSON: < garbage >")
        monkeypatch.setattr(wre_mcp, "_session", _protocol_broken)
        resp = _result_shape(wre_mcp.wre_status("TEST", "job-1"))
        assert resp["error"] == "transport"  # RpcError family contract
        assert "did not return JSON" in resp["message"]

    def test_session_call_level_exception_is_contained(self, wre_mcp, monkeypatch):
        """The session exists but .call itself explodes mid-round-trip."""
        class _MidCallBomb:
            def call(self, action, payload, timeout_ms=None):
                raise ValueError("corrupt internal state")
        monkeypatch.setattr(wre_mcp, "_session",
                            lambda target_name, entry_root: _MidCallBomb())
        resp = _result_shape(wre_mcp.wre_tasks("TEST"))
        assert resp["error"] == "internal"
        assert "ValueError" in resp["message"]
