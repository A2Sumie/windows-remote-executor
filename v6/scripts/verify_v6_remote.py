"""WRE v6 verify matrix — runs against a deployed target via rpc-stdio.

Usage:
    PYTHONPATH=. python3 -m v6.scripts.verify_v6_remote <target>
    WRE_ENTRY=C:/WRE/wre6 PYTHONPATH=. python3 -m v6.scripts.verify_v6_remote X570
    (current X570 sidecar still lives at the legacy root:
     WRE_ENTRY=C:/CodexRemote/wre6 ... — see V6.md "Rebrand note")

All smoke payloads are harmless (echo/--version/ping-with-timeout). Every file
it creates lives under <wreRoot>/inbox/wre-v6-verify* and is removed again.
The task.create segment is OFF by default; enable with
WRE_VERIFY_INCLUDE_TASK_CREATE=1.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from v6.controller import client as rpc  # noqa: E402
from v6.controller import targets as tgt  # noqa: E402

EXPECTED_ACTIONS = 32  # design appendix A table (6 host + 1 system + 8 tasks + 8 file + 5 process + 4 wsl)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target")
    parser.add_argument("--entry", default=None,
                        help="remote entry root (default $WRE_ENTRY or C:/WRE/wre)")
    args = parser.parse_args(argv)
    target = tgt.load_target(args.target)
    entry = rpc.resolve_entry_root(args.entry)

    print(f"== WRE v6 verify: {target.name} ({target.ssh_destination}) entry={entry} ==\n")
    failures: list[str] = []
    wre_root = "C:/WRE"  # fallback until host.info returns the real wreRoot

    def step(name: str, action: str, payload: dict | None = None,
             timeout: int = 60) -> dict | None:
        try:
            call = rpc.call(target, action, payload or {}, timeout_seconds=timeout,
                            entry_root=entry)
        except rpc.RpcError as exc:
            print(f"  {name}: TRANSPORT ERROR -> {exc}")
            failures.append(f"{name}:transport")
            return None
        ok_flag = call.ok
        marker = "ok " if ok_flag else "FAIL"
        print(f"  [{marker}] {name}: action={action} errorClass={call.error_class}")
        if not ok_flag:
            print(f"        message: {(call.message or call.stderr_text)[:200]}")
            failures.append(f"{name}:{call.error_class}")
            return None
        return call.data

    # --- capabilities / version / schema surface ---
    data = step("capabilities", "host.capabilities")
    if data:
        ok = (str(data.get("build") or "").startswith("v6") and data.get("version") == 6
              and len(data.get("actions", [])) == EXPECTED_ACTIONS
              and len(data.get("schemas", {})) == EXPECTED_ACTIONS)
        print(f"        build={data.get('build')} version={data.get('version')} "
              f"actions={len(data.get('actions', []))} schemas={len(data.get('schemas', {}))}")
        if not ok:
            failures.append("capabilities:surface")

    data = step("host.info", "host.info")
    if data:
        wre_root = str(data.get("wreRoot") or wre_root)
        print(f"        whoami={data.get('whoami')} policy={data.get('policyLabel')}/"
              f"{data.get('policyStatus')} wreRoot={wre_root}")

    step("probe default", "host.probe", {"categories": ["os", "sshd", "policy"]})
    step("system.help", "system.help", {"action": "process.run"})

    # --- guard dry-run + netstat cross-check (v5 leftover, must-run on box) ---
    guard = step("guard dry", "host.guard", {"noDisable": True})
    netstat = step("netstat via process.run", "process.run",
                   {"exe": "C:/Windows/System32/netstat.exe", "args": ["-ano"],
                    "timeoutMs": 30000})
    if guard is not None and netstat is not None:
        listeners = guard.get("activeListenAddresses")
        known = guard.get("activeListenersKnown")
        listening_22 = [ln for ln in netstat.get("stdout", "").splitlines()
                        if "LISTENING" in ln and ":22 " in ln]
        ips = {m.group(1) for ln in listening_22
               for m in [re.search(r"TCP\s+(\d+\.\d+\.\d+\.\d+):22\s", ln)] if m}
        print(f"        guard.active={listeners} known={known} netstat:22-ips={sorted(ips)}")
        if known and listeners is not None and ips:
            if set(listeners) - {"0.0.0.0"} <= ips or "0.0.0.0" in listeners:
                print("        [ok ] guard/netstat listen addresses consistent")
            else:
                print("        [FAIL] guard lists addresses netstat does not show")
                failures.append("guard-netstat:mismatch")

    # --- process.run smoke + server-side timeout semantics ---
    data = step("process.run echo", "process.run",
                {"exe": "C:/Windows/System32/cmd.exe", "args": ["/d", "/c", "echo wre-v6-smoke"]})
    if data and data.get("stdout", "").strip() != "wre-v6-smoke":
        failures.append("process.run:stdout")
    data = step("process.run timeout", "process.run",
                {"exe": "C:/Windows/System32/ping.exe",
                 "args": ["-n", "30", "127.0.0.1"], "timeoutMs": 3000}, timeout=45)
    if data and data.get("timedOut") is not True:
        print("        [FAIL] expected timedOut=true")
        failures.append("process.run:timeout-semantics")

    # --- job smoke: start -> status -> wait -> tail ---
    # NOTE (cmd /c quoting): keep the command as separate argv words. A single
    # "echo A & echo B" string gets quoted by list2cmdline and then cmd's
    # first-and-last-quote stripping rule mangles it ("syntax of the command
    # is incorrect"). Split words compose to an unquoted remainder — safe.
    data = step("job start", "process.start",
                {"exe": "C:/Windows/System32/cmd.exe",
                 "args": ["/d", "/c", "echo", "wre-v6-job-start", "&",
                          "ping", "-n", "3", "127.0.0.1", "&", "echo", "wre-v6-job-done"]})
    job_id = (data or {}).get("jobId", "")
    if job_id:
        step("job status", "process.status", {"jobId": job_id})
        waited = step("job wait", "process.wait", {"jobId": job_id, "timeoutMs": 30000}, timeout=60)
        if waited and "wre-v6-job-done" not in waited.get("logTail", ""):
            print("        [FAIL] job log tail missing marker")
            failures.append("job:logtail")
        tail_read = step("job log via readText tail", "file.readText",
                         {"path": f"{wre_root}/jobs/{job_id}.log", "tail": 2000})
        if tail_read and "wre-v6-job-done" not in tail_read.get("text", ""):
            failures.append("job:readText-tail")

    # --- wsl ---
    wsl = step("wsl.list", "wsl.list", {}, timeout=45)
    if wsl is not None:
        distros = wsl.get("distros", [])
        print(f"        distros={len(distros)} {[d.get('name') for d in distros]}")
        if not distros:
            print("        (no WSL distros — wsl.run skipped)")
    # wsl.list returning not-found/remote-exception is recorded by step(); a
    # host without WSL surfaces as not-found which is acceptable-but-recorded.

    # --- inbox file smoke (self-cleaning) ---
    inbox = f"{wre_root}/inbox"
    step("write-text", "file.writeText", {"path": f"{inbox}/wre-v6-verify.txt", "text": "hello\n"})
    step("read-text", "file.readText", {"path": f"{inbox}/wre-v6-verify.txt"})
    step("mkdir", "file.mkdir", {"path": f"{inbox}/wre-v6-verify-dir"})
    step("delete-tree", "file.deleteTree", {"path": f"{inbox}/wre-v6-verify-dir"})
    step("copy", "file.copy", {"source": f"{inbox}/wre-v6-verify.txt",
                               "destination": f"{inbox}/wre-v6-verify-copy.txt"})
    step("delete-test-file", "file.deleteTree", {"path": f"{inbox}/wre-v6-verify.txt"})
    step("delete-test-copy", "file.deleteTree", {"path": f"{inbox}/wre-v6-verify-copy.txt"})

    # --- tasks.list summary (read-only) ---
    data = step("tasks.list summary", "host.tasks.list", {"limit": 10})
    if data:
        print(f"        count={data.get('count')} truncated={data.get('truncated')} "
              f"sampleFields={sorted((data.get('tasks') or [{}])[0].keys()) if data.get('tasks') else []}")

    # --- task.create segment: OFF by default ---
    if os.environ.get("WRE_VERIFY_INCLUDE_TASK_CREATE") == "1":
        can_create = True
        try:
            call = rpc.call(target, "host.task.create", {
                "name": "wre-v6-verify-once",
                "exe": "C:/Windows/System32/cmd.exe",
                "args": ["/d", "/c", f"echo wre-v6-verify > {inbox}/wre-v6-task.log"],
                "trigger": "manual", "run_as_user": "", "run_level": 1,
                "deleteAfterRun": True,
            }, timeout_seconds=30, entry_root=entry)
            if not call.ok:
                print(f"  [FAIL] create-task: {call.message or call.stderr_text}")
                failures.append("create-task:" + call.error_class)
                can_create = False
        except rpc.RpcError as exc:
            print(f"  create-task: TRANSPORT ERROR -> {exc}")
            failures.append("create-task:transport")
            can_create = False
        if can_create:
            print("  [ok ] create-task (deleteAfterRun)")
            step("run-task", "host.task.run", {"name": "wre-v6-verify-once"})
            import time as _t
            _t.sleep(2)
            step("read-task-output", "file.readText", {"path": f"{inbox}/wre-v6-task.log"})
            step("delete-task", "host.task.delete", {"name": "wre-v6-verify-once"})
            step("cleanup-task-output", "file.deleteTree", {"path": f"{inbox}/wre-v6-task.log"})

    print()
    if failures:
        print(f"FAIL — {len(failures)} step(s) failed: {failures}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
