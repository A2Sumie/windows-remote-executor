"""Host actions: probe, guard, repair, policy, tasks.*.

Pure Python: stdlib + pywin32 (for service config / sshd restart) + comtypes
(for TaskScheduler COM). No PowerShell, no argv to schtasks.exe / netsh.exe /
sc.exe. Firewall rules are read via HNetCfg.FwPolicy2 COM.

v4-hardening backports (2026-08-25, from v5/v6):
- host.repair restarts sshd only when the config was actually rewritten or
  the service is not Running (the old v4 unconditional restart made the
  10-minute repair watch kill long-lived SSH sessions for no reason).
- _uptime_hours uses kernel32 GetTickCount64 via ctypes (v6, commit 9ec7b75):
  pywin32 has no win32api.GetTickCount64, so the old code's bare except made
  uptimeHours silently report 0.0 forever.
- host.probe policy category reports policyStatus (ok/missing/corrupt) from
  the fail-closed access_policy backport; sshd category uses the real
  iphlpapi listener enumeration (see win32/sshd.py).
"""

from __future__ import annotations

import getpass
import os
import platform
import socket
import sys
import time
from datetime import datetime, timezone
from typing import Any

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(THIS_DIR))

from win32 import access_policy as ap  # noqa: E402
from win32 import sshd as sshd_mod  # noqa: E402
from win32 import service as svc_mod  # noqa: E402
from win32 import firewall as fw_mod  # noqa: E402
from win32 import scheduled_tasks as tasks_mod  # noqa: E402

REPAIR_TASK_NAMES = (
    "CodexRemote Sshd Repair Logon",
    "CodexRemote Sshd Repair Startup",
    "CodexRemote Sshd Repair Watch",
)

PROBE_DEFAULT_CATEGORIES = ("os", "sshd", "policy")
PROBE_ALL_CATEGORIES = ("os", "sshd", "policy", "tasks", "python")


# ---------- host.probe ----------

def _probe(payload: dict[str, Any]) -> dict[str, Any]:
    categories = payload.get("categories") or list(PROBE_DEFAULT_CATEGORIES)
    if isinstance(categories, str):
        categories = [categories]
    categories = [c for c in categories if c in PROBE_ALL_CATEGORIES]
    if not categories:
        categories = list(PROBE_DEFAULT_CATEGORIES)

    data: dict[str, Any] = {
        "timestamp": _now_iso(),
        "hostname": socket.gethostname(),
        "currentUser": getpass.getuser(),
        "protocolVersion": 4,
    }

    if "os" in categories:
        data["os"] = _probe_os()
    if "sshd" in categories:
        data["sshd"] = _probe_sshd()
    if "policy" in categories:
        policy, status = ap.read_policy()
        data["policy"] = {**(policy or {"label": "UNCONFIGURED"}), "policyStatus": status}
    if "tasks" in categories:
        try:
            data["tasks"] = tasks_mod.list_repair_tasks(REPAIR_TASK_NAMES)
        except Exception as exc:  # noqa: BLE001
            data["tasks"] = {"error": str(exc), "tasks": []}
    if "python" in categories:
        data["python"] = {
            "interpreter": sys.executable,
            "version": sys.version.split()[0],
            "platform": platform.platform(),
        }

    return {
        "data": data,
        "stdout_text": _json_compact(data) + "\n",
        "evidence_extra": ["probe:" + ",".join(categories)],
    }


def _probe_os() -> dict[str, Any]:
    info = {
        "caption": f"Windows {platform.release()} {platform.version()}",
        "build": platform.version(),
        "architecture": platform.machine(),
        "uptimeHours": _uptime_hours(),
        "timezone": time.tzname[0] if time.tzname else "",
    }
    try:
        import winreg  # type: ignore
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
        ) as key:
            for name, field in (
                ("ProductName", "productName"),
                ("DisplayVersion", "displayVersion"),
                ("CurrentBuild", "currentBuild"),
            ):
                try:
                    info[field] = winreg.QueryValueEx(key, name)[0]
                except FileNotFoundError:
                    pass
    except Exception:  # noqa: BLE001
        pass
    return info


def _uptime_hours() -> float:
    try:
        # pywin32 exposes only GetTickCount (32-bit, wraps); GetTickCount64
        # does not exist in pywin32 — use kernel32 via ctypes (the pre-hardening
        # code silently returned 0.0 forever).
        import ctypes
        tick_ms = ctypes.windll.kernel32.GetTickCount64()
        return round(tick_ms / 1000.0 / 3600.0, 2)
    except Exception:  # noqa: BLE001
        return 0.0


def _probe_sshd() -> dict[str, Any]:
    sshd_state = svc_mod.service_state_safe("sshd")
    tailscale_state = svc_mod.service_state_safe("Tailscale")
    configured = sshd_mod.read_listen_addresses()
    active = sshd_mod._active_listen_addresses(22)
    return {
        "port": 22,
        "configuredListenAddresses": configured,
        # None = enumeration unknown (non-Windows / iphlpapi failure).
        "activeListenAddresses": active if active is not None else [],
        "activeListenersKnown": active is not None,
        "services": {
            "sshd": sshd_state,
            "Tailscale": tailscale_state,
        },
    }


# ---------- host.guard ----------

def _guard(payload: dict[str, Any]) -> dict[str, Any]:
    no_disable = bool(payload.get("noDisable"))
    policy = ap.load_policy()
    expected = payload.get("expectedListenAddress") or policy.get("expectedListenAddress")
    log_path = payload.get("logPath") or "C:/CodexRemote/logs/sshd-guard.log"
    diagnostics = sshd_mod.evaluate_exposure(expected_listen=expected, no_disable=no_disable, log_path=log_path)
    return {
        "data": diagnostics,
        "stdout_text": _json_compact(diagnostics) + "\n",
        "evidence_extra": ["guard" + (":dry" if no_disable else "")],
    }


# ---------- host.repair ----------

def _repair(payload: dict[str, Any]) -> dict[str, Any]:
    policy = ap.load_policy()
    expected = payload.get("expectedListenAddress") or policy.get("expectedListenAddress")
    force_rewrite = bool(payload.get("forceRewrite"))
    log_path = payload.get("logPath") or "C:/CodexRemote/logs/sshd-repair.log"

    steps: list[str] = []
    rewritten = False
    try:
        rewrite_result = sshd_mod.rewrite_config(expected_listen=expected, force=force_rewrite, log_path=log_path)
        rewritten = bool(rewrite_result.get("rewritten"))
        steps.append("sshd_config.rewritten" if rewritten else "sshd_config.unchanged")
    except Exception as exc:  # noqa: BLE001
        steps.append(f"sshd_config.error:{exc}")

    # Restart sshd only when there is something to pick up (config rewritten)
    # or the service is not Running. The pre-hardening unconditional restart
    # made the 10-minute SYSTEM repair watch drop healthy long-lived SSH
    # sessions.
    sshd_state = svc_mod.service_state_safe("sshd").get("state")
    if rewritten or sshd_state != "Running":
        result = svc_mod.restart_service_safe("sshd")
        if result.get("action") == "restarted":
            steps.append("sshd.restarted")
        else:
            steps.append(f"sshd.restart.error:{result.get('error')}")
    else:
        steps.append("sshd.restart.skipped:config-unchanged-and-service-running")

    try:
        tasks_mod.ensure_repair_tasks(expected_listen=expected)
        steps.append("repair_tasks.ensured")
    except Exception as exc:  # noqa: BLE001
        steps.append(f"repair_tasks.error:{exc}")

    try:
        fw_mod.ensure_sshd_firewall_rule()
        steps.append("firewall.ensured")
    except Exception as exc:  # noqa: BLE001
        steps.append(f"firewall.skipped:{exc}")

    return {
        "data": {"steps": steps, "expectedListenAddress": expected},
        "stdout_text": _json_compact({"steps": steps}) + "\n",
        "evidence_extra": ["repair"],
    }


# ---------- host.policy ----------

def _policy(payload: dict[str, Any]) -> dict[str, Any]:
    written = ap.write_policy(payload)
    return {
        "data": {"policy": written, "path": ap._POLICY_PATH},
        "stdout_text": _json_compact({"policy": written}) + "\n",
        "evidence_extra": ["policy"],
    }


# ---------- host.tasks.list / detail ----------

def _tasks_list(payload: dict[str, Any]) -> dict[str, Any]:
    prefix = payload.get("prefix") or "CodexRemote"
    names = payload.get("taskNames") or []
    items = tasks_mod.list_tasks(prefix=prefix, names=names)
    return {
        "data": {"tasks": items},
        "stdout_text": _json_compact({"tasks": items}) + "\n",
        "evidence_extra": ["tasks.list:" + prefix],
    }


def _tasks_detail(payload: dict[str, Any]) -> dict[str, Any]:
    name = payload.get("name")
    if not name:
        raise ValueError("host.tasks.detail requires payload.name")
    item = tasks_mod.detail_task(name)
    return {
        "data": {"task": item},
        "stdout_text": _json_compact({"task": item}) + "\n",
        "evidence_extra": ["tasks.detail"],
    }


def _task_run(payload: dict[str, Any]) -> dict[str, Any]:
    name = payload.get("name")
    if not name:
        raise ValueError("host.task.run requires payload.name")
    result = tasks_mod.run_task(name)
    return {
        "data": result,
        "stdout_text": _json_compact(result) + "\n",
        "evidence_extra": ["task.run:" + name],
    }


def _task_create(payload: dict[str, Any]) -> dict[str, Any]:
    result = tasks_mod.create_task(payload)
    return {
        "data": result,
        "stdout_text": _json_compact(result) + "\n",
        "evidence_extra": ["task.create:" + str(payload.get("name"))],
    }


def _task_update(payload: dict[str, Any]) -> dict[str, Any]:
    result = tasks_mod.update_task(payload)
    return {
        "data": result,
        "stdout_text": _json_compact(result) + "\n",
        "evidence_extra": ["task.update:" + str(payload.get("name"))],
    }


def _task_delete(payload: dict[str, Any]) -> dict[str, Any]:
    name = payload.get("name")
    if not name:
        raise ValueError("host.task.delete requires payload.name")
    result = tasks_mod.delete_task(name)
    return {
        "data": result,
        "stdout_text": _json_compact(result) + "\n",
        "evidence_extra": ["task.delete:" + name],
    }


def _tasks_apply(payload: dict[str, Any]) -> dict[str, Any]:
    """Trigger the SYSTEM `CodexRemote WRE Apply` agent to re-register all
    managed tasks. Requires that agent to have been installed once (elevated).
    No elevation needed to trigger it."""
    result = tasks_mod.run_task("CodexRemote WRE Apply")
    return {
        "data": result,
        "stdout_text": _json_compact(result) + "\n",
        "evidence_extra": ["tasks.apply"],
    }


# ---------- utils ----------

def _json_compact(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# sibling-importable when rpc.py calls host_actions.register
if False:  # pragma: no cover
    pass


REGISTRATIONS = (
    ("host.probe", _probe),
    ("host.guard", _guard),
    ("host.repair", _repair),
    ("host.policy", _policy),
    ("host.tasks.list", _tasks_list),
    ("host.tasks.detail", _tasks_detail),
    ("host.task.create", _task_create),
    ("host.task.update", _task_update),
    ("host.task.run", _task_run),
    ("host.task.delete", _task_delete),
    ("host.tasks.apply", _tasks_apply),
)
