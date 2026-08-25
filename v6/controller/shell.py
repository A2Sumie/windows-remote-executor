"""WRE v6 controller shell — single entrypoint.

Usage:
    python3 -m v6.controller.shell <target> <action> [payload-json]
    python3 -m v6.controller.shell <target> --probe [...]
    python3 -m v6.controller.shell <target> --repl            (session mode)
    python3 -m v6.controller.shell <target> --session <action> <json> [...]

v6 additions:
  --session   hold ONE ssh connection for every call in this invocation
              (native loop mode amortizes Python/COM startup); the REPL is
              always session-backed.
  --entry R   remote entry root (default $WRE_ENTRY or C:/WRE/wre);
              use --entry C:/WRE/wre6 for a sidecar deploy (legacy fleet:
              C:/CodexRemote/wre, current X570 sidecar: C:/CodexRemote/wre6).
  --info      shortcut: host.info (v6 cheap self-check).
  On startup against a v6-only action, the peer version is checked once and a
  v4/v5 peer produces an upgrade hint instead of a bare "unsupported action".
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import client as rpc
from . import targets as tgt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wrev6", description="WRE v6 controller")
    parser.add_argument("target")
    parser.add_argument("action", nargs="?", help="rpc action name (use --repl for interactive)")
    parser.add_argument("payload", nargs="?", help="json payload or @file.json")
    parser.add_argument("--probe", action="store_true", help="shortcut: host.probe with default categories")
    parser.add_argument("--probe-tasks", action="store_true", help="shortcut: host.probe + tasks")
    parser.add_argument("--tasks", action="store_true", help="shortcut: host.tasks.list")
    parser.add_argument("--guard", action="store_true", help="shortcut: host.guard noDisable")
    parser.add_argument("--repair", action="store_true", help="shortcut: host.repair")
    parser.add_argument("--info", action="store_true", help="shortcut: host.info (v6)")
    parser.add_argument("--repl", action="store_true", help="interactive REPL (session-backed)")
    parser.add_argument("--session", action="store_true",
                        help="hold one ssh connection for all calls in this invocation")
    parser.add_argument("--entry", default=None,
                        help="remote entry root (default $WRE_ENTRY or C:/WRE/wre)")
    parser.add_argument("--out", help="write stdoutText to file")
    parser.add_argument("--timeout", type=int, default=None, help="timeout, seconds")
    args = parser.parse_args(argv)

    target = tgt.load_target(args.target)

    if args.repl or (args.action is None and not any(
            [args.probe, args.probe_tasks, args.tasks, args.guard, args.repair, args.info])):
        return _repl(target, entry_root=args.entry)

    if args.probe:
        return _run(target, "host.probe", {"categories": ["os", "sshd", "policy"]}, args.out, args.timeout, args)
    if args.probe_tasks:
        return _run(target, "host.probe", {"categories": ["os", "sshd", "policy", "tasks"]}, args.out, args.timeout, args)
    if args.tasks:
        return _run(target, "host.tasks.list", {}, args.out, args.timeout, args)
    if args.guard:
        return _run(target, "host.guard", {"noDisable": True}, args.out, args.timeout, args)
    if args.repair:
        return _run(target, "host.repair", {}, args.out, args.timeout, args)
    if args.info:
        return _run(target, "host.info", {}, args.out, args.timeout, args)
    if args.action:
        payload = _load_payload(args.payload)
        return _run(target, args.action, payload, args.out, args.timeout, args)

    parser.print_help()
    return 2


def _load_payload(spec: str | None) -> dict[str, Any]:
    if not spec:
        return {}
    if spec.startswith("@"):
        with open(spec[1:], "r", encoding="utf-8") as fh:
            return json.load(fh)
    return json.loads(spec)


def _run(target, action: str, payload: dict[str, Any], out: str | None,
         timeout: int | None, args: argparse.Namespace) -> int:
    if args.session:
        with rpc.Session(target, entry_root=args.entry) as sess:
            call = sess.call(action, payload,
                             timeout_ms=timeout * 1000 if timeout else None)
    else:
        # v6-only action against an unknown peer: cheap one-time version gate
        # so a v4/v5 host yields an actionable upgrade hint.
        if rpc._is_v6_only(action):
            rpc.check_peer_version(target, entry_root=args.entry)
        call = rpc.call(target, action, payload, timeout_seconds=timeout,
                        entry_root=args.entry)
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(call.stdout_text)
    print(json.dumps(call.response, ensure_ascii=False, indent=2))
    return 0 if call.ok else 1


def _repl(target, *, entry_root: str | None) -> int:
    print(f"# WRE v6 REPL — target={target.name} ({target.ssh_destination}) entry={rpc.resolve_entry_root(entry_root)}")
    print("# commands: help, quit, actions, info, list-targets, <action> <json>")
    with rpc.Session(target, entry_root=entry_root) as sess:
        sess.peer_version = rpc.check_peer_version(target, entry_root=entry_root)
        while True:
            try:
                line = input(f"wrev6 [{target.name}]> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line in ("quit", "exit", "q"):
                break
            if line == "help":
                print("try: actions | info | list-targets | process.run {\"exe\":...,\"args\":[...]}")
                continue
            if line == "actions":
                call = sess.call("host.capabilities")
                print(json.dumps(call.data.get("actions"), ensure_ascii=False, indent=2))
                continue
            if line == "info":
                print(json.dumps(sess.call("host.info").data, ensure_ascii=False, indent=2))
                continue
            if line == "list-targets":
                print("\n".join(tgt.list_targets()))
                continue
            action, _, rest = line.partition(" ")
            try:
                payload = _load_payload(rest) if rest else {}
            except json.JSONDecodeError as exc:
                print(f"bad json: {exc}")
                continue
            try:
                call = sess.call(action, payload)
            except rpc.RpcError as exc:
                print(f"error: {exc}")
                continue
            print(json.dumps(call.response, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    # `python3 -m v6.controller.shell` (repo root on PYTHONPATH) is the only
    # supported entry; direct-file execution cannot resolve the relative imports.
    sys.exit(main())
