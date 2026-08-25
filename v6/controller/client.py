"""WRE v6 controller-side client.

One-shot `call()` keeps the v5 shape: one SSH process, one request line, one
response line, stdin closed (the v6 native loop treats EOF as shutdown).

`Session` holds ONE SSH connection open and pipes many request lines through
it (the v6 loop mode on the other end amortizes Python+COM startup across the
whole session). A broken pipe triggers exactly ONE automatic reconnect.

Remote entry root resolution order (sidecar/flip support):
    explicit entry_root argument > $WRE_ENTRY > default C:/WRE/wre
    (legacy fleet tree: C:/CodexRemote/wre — still reachable via WRE_ENTRY)
The remote command is fixed to:
    cmd.exe /d /s /c "<entry>/python/python.exe -I -X utf8 <entry>/rpc.py rpc-stdio"
The payload never travels in argv.

Version negotiation (design §5.6): `check_peer_version()` reads
host.capabilities; `Session.call`/`call()` of a v6-only action family against
a v4/v5 peer raises RpcProtocolError with an upgrade hint instead of a bare
"unsupported action".
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass
from typing import Any

from .targets import Target


DEFAULT_ENTRY_ROOT = "C:/WRE/wre"

# H9: a caller that passes neither timeout_ms nor timeout_seconds used to run
# ssh with NO deadline — a wedged host pinned the caller forever.
DEFAULT_TRANSPORT_TIMEOUT_S = 600

# Action families that exist only on a v6+ peer.
V6_ONLY_PREFIXES = ("process.", "wsl.")
V6_ONLY_ACTIONS = {"host.info", "system.help", "host.tasks.clean"}


def resolve_entry_root(entry_root: str | None = None) -> str:
    root = (entry_root or os.environ.get("WRE_ENTRY") or DEFAULT_ENTRY_ROOT).rstrip("/\\")
    return root


def remote_command(entry_root: str | None = None) -> str:
    root = resolve_entry_root(entry_root)
    return f'cmd.exe /d /s /c "{root}/python/python.exe -I -X utf8 {root}/rpc.py rpc-stdio"'


class RpcError(Exception):
    pass


class RpcTransportError(RpcError):
    pass


class RpcProtocolError(RpcError):
    pass


@dataclass
class Call:
    target: Target
    request: dict[str, Any]
    response: dict[str, Any]
    argv: list[str]
    returncode: int

    @property
    def ok(self) -> bool:
        return bool(self.response.get("ok"))

    @property
    def error_class(self) -> str:
        return str(self.response.get("errorClass") or "")

    @property
    def data(self) -> dict[str, Any]:
        return self.response.get("data") or {}

    @property
    def message(self) -> str:
        return str(self.response.get("message") or "")

    @property
    def stdout_text(self) -> str:
        # v6 slimming: stdoutText is opt-in. Fall back to data.stdout
        # (process.run / wsl.run) or data.text (file.readText).
        text = str(self.response.get("stdoutText") or "")
        if not text:
            data = self.data
            text = str(data.get("stdout") or data.get("text") or "")
        return text

    @property
    def stderr_text(self) -> str:
        text = str(self.response.get("stderrText") or "")
        if not text:
            text = str(self.data.get("stderr") or "")
        return text

    def require_ok(self) -> "Call":
        if not self.ok:
            raise RpcError(f"{self.error_class}: {self.message or self.stderr_text}")
        return self


def build_request(
    action: str,
    payload: dict[str, Any] | None,
    *,
    request_id: str | None = None,
    timeout_ms: int | None = None,
    timeout_seconds: int | None = None,
    access_token: str | None,
) -> dict[str, Any]:
    if not action:
        raise RpcError("action is required")
    request: dict[str, Any] = {
        "id": request_id or f"rpc-{uuid.uuid4().hex}",
        "action": action,
        "payload": payload or {},
    }
    ms = timeout_ms if timeout_ms is not None else (
        timeout_seconds * 1000 if timeout_seconds else None)
    if ms:
        request["timeoutMs"] = ms
    if access_token:
        request["accessToken"] = access_token
    return request


def parse_response(stdout: str, stderr: str, returncode: int) -> dict[str, Any]:
    candidates = [stdout.lstrip("\ufeff").strip()]
    lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
    if len(lines) > 1:
        candidates.extend([lines[-1].lstrip("\ufeff"), lines[0].lstrip("\ufeff")])

    for cand in candidates:
        if not cand:
            continue
        try:
            response = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(response, dict):
            return response
        raise RpcProtocolError("rpc-stdio response is not a JSON object")

    detail = stderr.strip() or stdout.strip() or f"ssh exit code {returncode}"
    raise RpcProtocolError(f"rpc-stdio did not return JSON: {detail}")


def _is_v6_only(action: str) -> bool:
    return action.startswith(V6_ONLY_PREFIXES) or action in V6_ONLY_ACTIONS


def _upgrade_hint(call_resp: dict[str, Any], action: str) -> None:
    """Rewrite a bare v4/v5 'unsupported action' into an actionable message."""
    if (not call_resp.get("ok")
            and call_resp.get("errorClass") == "unsupported"
            and _is_v6_only(action)
            and int(call_resp.get("protocolVersion") or 0) < 6):
        peer = call_resp.get("protocolVersion")
        call_resp["message"] = (
            f"{action} requires a v6+ native on the host, but the peer speaks "
            f"v{peer}. Deploy v6 (scripts/deploy_sftp.py) or use the v4/v5 "
            "action set."
        )


def call(
    target: Target,
    action: str,
    payload: dict[str, Any] | None = None,
    *,
    request_id: str | None = None,
    timeout_seconds: int | None = None,
    timeout_ms: int | None = None,
    entry_root: str | None = None,
) -> Call:
    request = build_request(
        action, payload,
        request_id=request_id,
        timeout_ms=timeout_ms,
        timeout_seconds=timeout_seconds,
        access_token=target.access_token,
    )
    transport = DEFAULT_TRANSPORT_TIMEOUT_S
    if timeout_ms:
        transport = timeout_ms // 1000 + 30
    elif timeout_seconds:
        transport = timeout_seconds + 30
    return call_request(target, request, transport_timeout_seconds=transport,
                        entry_root=entry_root)


def call_request(target: Target, request: dict[str, Any], *,
                 transport_timeout_seconds: int | None,
                 entry_root: str | None = None) -> Call:
    stdin_text = json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n"
    argv = [*target.ssh_args, target.ssh_destination, remote_command(entry_root)]
    try:
        completed = subprocess.run(
            argv,
            input=stdin_text,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=transport_timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RpcTransportError(
            f"rpc transport timed out after {transport_timeout_seconds}s"
        ) from exc

    response = parse_response(completed.stdout, completed.stderr, completed.returncode)
    _upgrade_hint(response, str(request.get("action") or ""))
    return Call(target=target, request=request, response=response, argv=argv,
                returncode=completed.returncode)


def check_peer_version(target: Target, *, entry_root: str | None = None) -> int:
    """Return the peer protocol version (0 when undeterminable). Prints an
    upgrade hint to stderr when the peer is older than v6."""
    try:
        c = call(target, "host.capabilities", {}, timeout_seconds=20, entry_root=entry_root)
        version = int(c.data.get("version") or c.response.get("protocolVersion") or 0)
    except RpcError:
        return 0
    if 0 < version < 6:
        print(f"[wre-v6] note: {target.name} runs protocol v{version}; "
              "v6-only actions (process.*/wsl.*/host.info/system.help/"
              "tasks.clean) will fail until the host is upgraded.",
              file=sys.stderr)
    return version


class Session:
    """One SSH connection, many calls (native loop mode). Auto-reconnects
    exactly once per call on transport failure."""

    def __init__(self, target: Target, *, entry_root: str | None = None,
                 stderr_echo: bool = False) -> None:
        self.target = target
        self.entry_root = resolve_entry_root(entry_root)
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._stderr_echo = stderr_echo
        self.peer_version: int | None = None

    # -- connection lifecycle --

    def _argv(self) -> list[str]:
        return [*self.target.ssh_args, self.target.ssh_destination,
                remote_command(self.entry_root)]

    def _open(self) -> None:
        self.close()
        self._proc = subprocess.Popen(
            self._argv(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE if not self._stderr_echo else None,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    def __enter__(self) -> "Session":
        self._open()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- calling --

    def call(self, action: str, payload: dict[str, Any] | None = None, *,
             timeout_ms: int | None = None) -> Call:
        request = build_request(action, payload, request_id=None,
                                timeout_ms=timeout_ms,
                                access_token=self.target.access_token)
        with self._lock:
            try:
                response = self._round_trip(request)
            except (BrokenPipeError, RpcTransportError):
                # ONE automatic reconnect, then surface the failure.
                self._open()
                response = self._round_trip(request)
        _upgrade_hint(response, action)
        return Call(target=self.target, request=request, response=response,
                    argv=self._argv(), returncode=0)

    def _round_trip(self, request: dict[str, Any]) -> dict[str, Any]:
        if self._proc is None or self._proc.poll() is not None:
            self._open()
        assert self._proc is not None and self._proc.stdin and self._proc.stdout
        line = json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            self._proc.stdin.write(line)
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise RpcTransportError(f"session write failed: {exc}") from exc

        # Read with a deadline (request timeoutMs + 30s transport margin,
        # default 600s): a hung peer must not pin the agent forever.
        timeout_ms = request.get("timeoutMs")
        deadline_s = (int(timeout_ms) // 1000 + 30) if timeout_ms else 600
        result: dict[str, Any] = {}
        proc = self._proc

        def _reader() -> None:
            try:
                result["line"] = proc.stdout.readline() if proc and proc.stdout else ""
            except Exception as exc:  # noqa: BLE001
                result["error"] = exc

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        t.join(timeout=deadline_s)
        if t.is_alive():
            self.close()  # kill the stuck ssh so the next call reconnects
            raise RpcTransportError(f"session read timed out after {deadline_s}s")
        if "error" in result:
            raise RpcTransportError(f"session read failed: {result['error']}")
        raw = str(result.get("line") or "")
        if not raw:
            stderr_tail = ""
            try:
                if self._proc and self._proc.stderr:
                    stderr_tail = self._proc.stderr.read()[-400:]
            except Exception:  # noqa: BLE001
                pass
            raise RpcTransportError(
                f"session closed by peer (ssh exit {self._proc.poll() if self._proc else '?'}): {stderr_tail}"
            )
        return parse_response(raw, "", 0)
