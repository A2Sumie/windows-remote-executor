"""Chaos-matrix fixtures: PATH-shim fake ssh + E1-E6 assertion helpers.

The shim (chaos_ssh.py, sibling file) is exposed as `ssh` by prepending a
generated bin dir to PATH — the literal "ssh" in Target.ssh_args then resolves
to it for BOTH controller links (subprocess.run one-shot and Session Popen).
Zero network, zero real hosts: every byte stays in local pipes.

Determinism: modes/params via env, seeded garbage, counter files for any
cross-process state. Wall-clock assertions use fixed deadlines with generous
margins (assert elapsed < bound; lower bounds at 50% of the injected delay).

Assertion vocabulary (saFUZZ-fuzzing.md §3.2):
  E1 structured error   - typed Rpc*Error / structured {"error":...}, no traceback
  E2 bounded timeout    - measured wall-clock inside [lo, hi]
  E3 exactly-once       - write-phase break -> ONE reconnect+resend, peer saw 1 request
  E4 no auto-resend     - read-phase break -> error, peer request count stays 1
  E5 never hangs        - the call returns inside an outer watchdog
  E6 self-healing       - after the fault lifts, the next call succeeds
"""

from __future__ import annotations

import json
import os
import stat
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

import pytest

from v6.controller import client as rpc

CHAOS_DIR = Path(__file__).resolve().parent
SHIM_SCRIPT = CHAOS_DIR / "chaos_ssh.py"


class ChaosShim:
    """Handle over one installed fake-ssh shim + its state dir."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir

    # -- peer-side observability --

    def _read_log(self, name: str) -> list[str]:
        path = self.state_dir / name
        if not path.is_file():
            return []
        return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln]

    def connect_count(self) -> int:
        return len(self._read_log("connects.log"))

    def requests(self) -> list[dict[str, Any]]:
        return [json.loads(ln) for ln in self._read_log("requests.log")]

    def request_count(self) -> int:
        return len(self.requests())

    def argv_seen(self) -> list[list[str]]:
        return [json.loads(ln)["argv"] for ln in self._read_log("connects.log")]


@pytest.fixture()
def chaos_shim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ChaosShim:
    """Install the fake ssh on PATH and return its observability handle."""
    bin_dir = tmp_path / "shimbin"
    bin_dir.mkdir()
    wrapper = bin_dir / "ssh"
    wrapper.write_text(
        f"#!/bin/sh\nexec {sys.executable} {SHIM_SCRIPT} \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    state_dir = tmp_path / "chaos-state"
    state_dir.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("CHAOS_STATE_DIR", str(state_dir))
    monkeypatch.setenv("CHAOS_SEED", "20260917")
    monkeypatch.setenv("CHAOS_MODE", "ok")
    return ChaosShim(state_dir)


@pytest.fixture()
def set_mode(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Switch the injected scenario. Params are a JSON dict of mode knobs."""
    def _set(mode: str, **params: Any) -> None:
        monkeypatch.setenv("CHAOS_MODE", mode)
        monkeypatch.setenv("CHAOS_PARAMS", json.dumps(params))
    return _set


# ---------- run helpers (both controller links) ----------

class Outcome:
    """Result of one chaos cell execution."""

    def __init__(self) -> None:
        self.call: rpc.Call | None = None
        self.error: BaseException | None = None
        self.elapsed_s: float = -1.0
        self.returned: bool = False  # False == the watchdog caught a hang (E5)


def run_one_shot(target, action: str = "host.info",
                 payload: dict | None = None,
                 transport_timeout_s: int = 10,
                 watchdog_s: float = 60.0) -> Outcome:
    """Drive the subprocess.run link with an outer E5 watchdog."""
    request = rpc.build_request(action, payload, access_token=target.access_token)
    out = Outcome()

    def _go() -> None:
        try:
            out.call = rpc.call_request(
                target, request, transport_timeout_seconds=transport_timeout_s)
        except BaseException as exc:  # noqa: BLE001 - the cell asserts on the type
            out.error = exc

    t0 = time.monotonic()
    t = threading.Thread(target=_go, daemon=True)
    t.start()
    t.join(timeout=watchdog_s)
    out.elapsed_s = time.monotonic() - t0
    out.returned = not t.is_alive()
    return out


def run_session_call(sess: rpc.Session, action: str = "host.info",
                     payload: dict | None = None,
                     timeout_ms: int | None = 30_000,
                     watchdog_s: float = 60.0) -> Outcome:
    """Drive the Session (Popen) link with an outer E5 watchdog."""
    out = Outcome()

    def _go() -> None:
        try:
            out.call = sess.call(action, payload, timeout_ms=timeout_ms)
        except BaseException as exc:  # noqa: BLE001
            out.error = exc

    t0 = time.monotonic()
    t = threading.Thread(target=_go, daemon=True)
    t.start()
    t.join(timeout=watchdog_s)
    out.elapsed_s = time.monotonic() - t0
    out.returned = not t.is_alive()
    return out


# ---------- E-assertions (deliberately throwable; the negative control eats them) ----------

def expect_ok(out: Outcome) -> rpc.Call:
    assert out.returned, f"E5 violated: call hung past the watchdog ({out.elapsed_s:.1f}s)"
    assert out.error is None, f"expected success, got {type(out.error).__name__}: {out.error}"
    assert out.call is not None and out.call.ok, f"expected ok call, got {out.call!r}"
    return out.call


def expect_error(out: Outcome, exc_class: type,
                 match: str = "", exact: bool = True) -> BaseException:
    """E1: structured error of the expected class; never a bare traceback.

    exact=True pins the precise class (default — phase semantics matter);
    exact=False accepts subclasses for cells where the OS-level race legitimately
    picks between a write-phase and a read-phase transport failure."""
    assert out.returned, f"E5 violated: call hung past the watchdog ({out.elapsed_s:.1f}s)"
    assert out.error is not None, f"expected {exc_class.__name__}, but the call succeeded"
    if exact:
        assert type(out.error) is exc_class, (
            f"expected exactly {exc_class.__name__}, got {type(out.error).__name__}: {out.error}")
    else:
        assert isinstance(out.error, exc_class), (
            f"expected {exc_class.__name__} family, got {type(out.error).__name__}: {out.error}")
    assert "Traceback (most recent call last)" not in str(out.error)
    if match:
        assert match in str(out.error), f"{match!r} not in {out.error}"
    return out.error


def expect_bounded(out: Outcome, lo_s: float, hi_s: float) -> None:
    """E2: measured wall-clock inside the deadline envelope."""
    assert out.returned, f"E5 violated: call hung past the watchdog ({out.elapsed_s:.1f}s)"
    assert out.elapsed_s <= hi_s, (
        f"E2 violated: {out.elapsed_s:.2f}s exceeded bound {hi_s:.2f}s")
    assert out.elapsed_s >= lo_s, (
        f"suspiciously fast: {out.elapsed_s:.2f}s < {lo_s:.2f}s — was the fault even injected?")


def expect_peer_requests(shim: ChaosShim, n: int) -> None:
    """E3/E4: the peer processed exactly n requests (no silent duplication)."""
    got = shim.request_count()
    assert got == n, f"peer executed {got} requests, expected {n} (duplication or loss!)"


def expect_connects(shim: ChaosShim, lo: int, hi: int) -> None:
    """Reconnect storm bound: linear, never exponential/unbounded."""
    got = shim.connect_count()
    assert lo <= got <= hi, f"connect count {got} outside [{lo}, {hi}]"


def expect_payload_never_in_argv(shim: ChaosShim) -> None:
    for argv in shim.argv_seen():
        blob = " ".join(argv)
        assert "host.info" not in blob and "payload" not in blob, (
            f"payload leaked into argv: {argv}")
