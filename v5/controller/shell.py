"""WRE v5 controller shell — single entrypoint.

Usage:
    python3 -m v5.controller.shell <target> <action> [payload-json]
    python3 -m v5.controller.shell <target> --probe [...]
    python3 -m v5.controller.shell <target> --repl
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import client as rpc
from . import targets as tgt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wrev5", description="WRE v5 controller")
    parser.add_argument("target")
    parser.add_argument("action", nargs="?", help="rpc action name (use --repl for interactive)")
    parser.add_argument("payload", nargs="?", help="json payload or @file.json")
    parser.add_argument("--probe", action="store_true", help="shortcut: host.probe with default categories")
    parser.add_argument("--probe-tasks", action="store_true", help="shortcut: host.probe + tasks")
    parser.add_argument("--tasks", action="store_true", help="shortcut: host.tasks.list")
    parser.add_argument("--guard", action="store_true", help="shortcut: host.guard noDisable")
    parser.add_argument("--repair", action="store_true", help="shortcut: host.repair")
    parser.add_argument("--repl", action="store_true", help="interactive REPL")
    parser.add_argument("--out", help="write stdoutText to file")
    parser.add_argument("--timeout", type=int, default=None)
    args = parser.parse_args(argv)

    target = tgt.load_target(args.target)

    if args.repl or (args.action is None and not any([args.probe, args.probe_tasks, args.tasks, args.guard, args.repair])):
        return _repl(target)

    if args.probe:
        return _run(target, "host.probe", {"categories": ["os", "sshd", "policy"]}, args.out, args.timeout)
    if args.probe_tasks:
        return _run(target, "host.probe", {"categories": ["os", "sshd", "policy", "tasks"]}, args.out, args.timeout)
    if args.tasks:
        return _run(target, "host.tasks.list", {"prefix": "CodexRemote"}, args.out, args.timeout)
    if args.guard:
        return _run(target, "host.guard", {"noDisable": True}, args.out, args.timeout)
    if args.repair:
        return _run(target, "host.repair", {}, args.out, args.timeout)
    if args.action:
        payload = _load_payload(args.payload)
        return _run(target, args.action, payload, args.out, args.timeout)

    parser.print_help()
    return 2


def _load_payload(spec: str | None) -> dict[str, Any]:
    if not spec:
        return {}
    if spec.startswith("@"):
        with open(spec[1:], "r", encoding="utf-8") as fh:
            return json.load(fh)
    return json.loads(spec)


def _run(target, action: str, payload: dict[str, Any], out: str | None, timeout: int | None) -> int:
    call = rpc.call(target, action, payload, timeout_seconds=timeout)
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(call.stdout_text)
    print(json.dumps(call.response, ensure_ascii=False, indent=2))
    return 0 if call.ok else 1


def _repl(target) -> int:
    print(f"# WRE v5 REPL — target={target.name} ({target.ssh_destination})")
    print("# commands: help, quit, actions, list-targets, <action> <json>")
    while True:
        try:
            line = input(f"wrev5 [{target.name}]> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in ("quit", "exit", "q"):
            break
        if line == "help":
            print("try: actions | list-targets | host.capabilities | host.probe {}")
            continue
        if line == "actions":
            call = rpc.call(target, "host.capabilities")
            print(json.dumps(call.data, ensure_ascii=False, indent=2))
            continue
        if line == "list-targets":
            print("\n".join(tgt.list_targets()))
            continue
        action, _, rest = line.partition(" ")
        payload = _load_payload(rest) if rest else {}
        try:
            call = rpc.call(target, action, payload)
        except rpc.RpcError as exc:
            print(f"error: {exc}")
            continue
        print(json.dumps(call.response, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    # Note: `python3 -m v5.controller.shell` (with the repo root on PYTHONPATH)
    # is the only supported entry. Direct-file execution
    # (`python3 v5/controller/shell.py`) cannot work: the relative imports at
    # the top of this file resolve before any __main__ sys.path fixup could
    # run, so the v4-era path hack was dead code and has been removed.
    sys.exit(main())
