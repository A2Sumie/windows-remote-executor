"""WRE v4 verify matrix — runs against a deployed target via rpc-stdio.

Usage: python3 -m wrev4.scripts.verify_v4_remote <target>
       python3 v4/scripts/verify_v4_remote.py <target>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
sys.path.insert(0, str(HERE.parent))

from controller import client as rpc
from controller import targets as tgt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target")
    args = parser.parse_args(argv)
    target = tgt.load_target(args.target)

    print(f"== WRE v4 verify: {target.name} ({target.ssh_destination}) ==\n")
    failures: list[str] = []

    def step(name: str, action: str, payload: dict | None = None) -> bool:
        try:
            call = rpc.call(target, action, payload or {}, timeout_seconds=30)
        except rpc.RpcError as exc:
            print(f"  {name}: TRANSPORT ERROR -> {exc}")
            failures.append(f"{name}:transport")
            return False
        ok_flag = call.ok
        marker = "ok " if ok_flag else "FAIL"
        print(f"  [{marker}] {name}: action={action} errorClass={call.error_class}")
        if not ok_flag:
            print(f"        stderr: {call.stderr_text[:200]}")
            failures.append(f"{name}:{call.error_class}")
        return ok_flag

    step("capabilities", "host.capabilities")
    step("probe default", "host.probe", {"categories": ["os", "sshd", "policy"]})
    step("probe tasks opt-in", "host.probe", {"categories": ["tasks", "policy"]})
    step("tasks.list", "host.tasks.list", {"prefix": "CodexRemote"})
    step("guard dry", "host.guard", {"noDisable": True})
    step("write-text", "file.writeText", {"path": "C:/CodexRemote/inbox/wre-v4-verify.txt", "text": "hello\n"})
    step("read-text", "file.readText", {"path": "C:/CodexRemote/inbox/wre-v4-verify.txt"})
    step("mkdir", "file.mkdir", {"path": "C:/CodexRemote/inbox/wre-v4-verify-dir"})
    step("delete-tree", "file.deleteTree", {"path": "C:/CodexRemote/inbox/wre-v4-verify-dir"})
    step("copy", "file.copy", {"source": "C:/CodexRemote/inbox/wre-v4-verify.txt",
                                "destination": "C:/CodexRemote/inbox/wre-v4-verify-copy.txt"})
    step("policy-read-back", "host.probe", {"categories": ["policy"]})
    step("delete-test-file", "file.deleteTree", {"path": "C:/CodexRemote/inbox/wre-v4-verify.txt"})
    step("delete-test-copy", "file.deleteTree", {"path": "C:/CodexRemote/inbox/wre-v4-verify-copy.txt"})

    # `host.task.create` requires an elevated caller token (RegisterTaskDefinition
    # fails with 0x8007052E from a non-elevated SSH session even with S4U/INTERACTIVE).
    # `host.tasks.list`/`detail` are read-only and work fine without elevation.
    # Run a quick "would-run" probe by invoking host.task.run on the existing
    # already-registered sshd-repair watch task and tolerating any guard-side
    # refusal — we only want to confirm the RPC action dispatch is wired.
    import os
    if os.environ.get("WRE_VERIFY_INCLUDE_TASK_CREATE") == "1":
        can_create_task = True
        try:
            call = rpc.call(target, "host.task.create", {
                "name": "wre-v4-verify-once",
                "exe": "C:/Windows/System32/cmd.exe",
                "args": ["/d", "/c", "echo wre-v4-verify > C:/CodexRemote/inbox/wre-v4-task.log"],
                "trigger": "manual",
                "run_as_user": "",
                "run_level": 1,
            }, timeout_seconds=30)
            ok_flag = call.ok
            marker = "ok " if ok_flag else "FAIL"
            print(f"  [{marker}] create-task: action=host.task.create errorClass={call.error_class}")
            if not ok_flag:
                print(f"        stderr: {call.stderr_text[:200]}")
                failures.append("create-task:" + call.error_class)
                can_create_task = False
        except rpc.RpcError as exc:
            print(f"  create-task: TRANSPORT ERROR -> {exc}")
            failures.append("create-task:transport")
            can_create_task = False

        if can_create_task:
            step("run-task", "host.task.run", {"name": "wre-v4-verify-once"})
            import time as _t
            _t.sleep(2)
            step("read-task-output", "file.readText",
                 {"path": "C:/CodexRemote/inbox/wre-v4-task.log"})
            step("delete-task", "host.task.delete", {"name": "wre-v4-verify-once"})
            step("cleanup-task-output", "file.deleteTree",
                 {"path": "C:/CodexRemote/inbox/wre-v4-task.log"})

    print()
    if failures:
        print(f"FAIL — {len(failures)} step(s) failed: {failures}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())