"""WRE v5 verify matrix — runs against a deployed target via rpc-stdio.

Usage: PYTHONPATH=. python3 -m v5.scripts.verify_v5_remote <target>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from v5.controller import client as rpc  # noqa: E402
from v5.controller import targets as tgt  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target")
    args = parser.parse_args(argv)
    target = tgt.load_target(args.target)

    print(f"== WRE v5 verify: {target.name} ({target.ssh_destination}) ==\n")
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
    step("write-text", "file.writeText", {"path": "C:/CodexRemote/inbox/wre-v5-verify.txt", "text": "hello\n"})
    step("read-text", "file.readText", {"path": "C:/CodexRemote/inbox/wre-v5-verify.txt"})
    step("mkdir", "file.mkdir", {"path": "C:/CodexRemote/inbox/wre-v5-verify-dir"})
    step("delete-tree", "file.deleteTree", {"path": "C:/CodexRemote/inbox/wre-v5-verify-dir"})
    step("copy", "file.copy", {"source": "C:/CodexRemote/inbox/wre-v5-verify.txt",
                                "destination": "C:/CodexRemote/inbox/wre-v5-verify-copy.txt"})
    step("policy-read-back", "host.probe", {"categories": ["policy"]})
    step("delete-test-file", "file.deleteTree", {"path": "C:/CodexRemote/inbox/wre-v5-verify.txt"})
    step("delete-test-copy", "file.deleteTree", {"path": "C:/CodexRemote/inbox/wre-v5-verify-copy.txt"})

    # host.task.create from the controller works when the host's token
    # filtering lets the SSH-logon user call RegisterTaskDefinition — this is
    # host-dependent (X570: works, ~180 SYSTEM tasks prove it; do not assume it
    # everywhere). The reliable SYSTEM path on any host is the apply-agent:
    # file.writeText apply-tasks.json + host.tasks.apply (once the elevated
    # deploy-wre has installed `CodexRemote WRE Apply`).
    # Gated behind WRE_VERIFY_INCLUDE_TASK_CREATE=1.
    if os.environ.get("WRE_VERIFY_INCLUDE_TASK_CREATE") == "1":
        can_create_task = True
        try:
            call = rpc.call(target, "host.task.create", {
                "name": "wre-v5-verify-once",
                "exe": "C:/Windows/System32/cmd.exe",
                "args": ["/d", "/c", "echo wre-v5-verify > C:/CodexRemote/inbox/wre-v5-task.log"],
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
            step("run-task", "host.task.run", {"name": "wre-v5-verify-once"})
            import time as _t
            _t.sleep(2)
            step("read-task-output", "file.readText",
                 {"path": "C:/CodexRemote/inbox/wre-v5-task.log"})
            step("delete-task", "host.task.delete", {"name": "wre-v5-verify-once"})
            step("cleanup-task-output", "file.deleteTree",
                 {"path": "C:/CodexRemote/inbox/wre-v5-task.log"})

    print()
    if failures:
        print(f"FAIL — {len(failures)} step(s) failed: {failures}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
