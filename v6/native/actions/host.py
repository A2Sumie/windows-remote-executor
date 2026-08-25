"""Host actions: info, probe, guard, repair, policy, tasks.*.

Pure Python: stdlib + pywin32 (for service config / sshd restart) + comtypes
(for TaskScheduler COM). No PowerShell, no argv to schtasks.exe / netsh.exe /
sc.exe. Firewall rules are read via HNetCfg.FwPolicy2 COM.

v6:
- host.info (cheap pre-flight self-check), host.tasks.clean (dryRun-first
  bulk cleanup of stale managed tasks), tasks.list summary-by-default with
  verbose/limit/olderThanDays, task.create deleteAfterRun/expiresAt.
- WRE_ROOT / TASK_PREFIX parameterize every path and task-name prefix.

v5: host.repair restarts sshd only when the config was actually rewritten or
the service is not Running (the v4 unconditional restart made the 10-minute
repair watch kill long-lived SSH sessions for no reason).
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

from version import (  # noqa: E402
    PROTOCOL_VERSION, BUILD, WRE_ROOT, TASK_PREFIX, LOGS_DIR,
    REPAIR_TASK_NAMES, APPLY_TASK_NAME,
)
from win32 import access_policy as ap  # noqa: E402
from win32 import sshd as sshd_mod  # noqa: E402
from win32 import service as svc_mod  # noqa: E402
from win32 import firewall as fw_mod  # noqa: E402
from win32 import scheduled_tasks as tasks_mod  # noqa: E402

PROBE_DEFAULT_CATEGORIES = ("os", "sshd", "policy")
PROBE_ALL_CATEGORIES = ("os", "sshd", "policy", "tasks", "python")


# ---------- host.info ----------

def _info(_payload: dict[str, Any]) -> dict[str, Any]:
    """Cheap self-check an agent can afford before every work session."""
    policy, status = ap.read_policy()
    data = {
        "timestamp": _now_iso(),
        "hostname": socket.gethostname(),
        "whoami": f"{os.environ.get('USERDOMAIN', '')}\\{getpass.getuser()}".lstrip("\\"),
        "protocolVersion": PROTOCOL_VERSION,
        "build": BUILD,
        "wreRoot": WRE_ROOT,
        "taskPrefix": TASK_PREFIX,
        "policyLabel": (policy or {}).get("label", "UNCONFIGURED"),
        "policyStatus": status,
        "uptimeHours": _uptime_hours(),
        "identity": "ssh-user (non-elevated); SYSTEM lane is host.task.*",
    }
    return {
        "data": data,
        "stdout_text": _json_compact(data) + "\n",
        "evidence_extra": ["info"],
    }


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
        "protocolVersion": PROTOCOL_VERSION,
        "build": BUILD,
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
        # pywin32 exposes only GetTickCount (32-bit, wraps at ~49.7 days);
        # GetTickCount64 must come from kernel32 via ctypes. The pre-v6.1
        # code called win32api.GetTickCount64 which does not exist, so
        # uptimeHours silently reported 0.0 forever.
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
    log_path = payload.get("logPath") or f"{LOGS_DIR}/sshd-guard.log"
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
    log_path = payload.get("logPath") or f"{LOGS_DIR}/sshd-repair.log"

    steps: list[str] = []
    rewritten = False
    try:
        rewrite_result = sshd_mod.rewrite_config(expected_listen=expected, force=force_rewrite, log_path=log_path)
        rewritten = bool(rewrite_result.get("rewritten"))
        steps.append("sshd_config.rewritten" if rewritten else "sshd_config.unchanged")
    except Exception as exc:  # noqa: BLE001
        steps.append(f"sshd_config.error:{exc}")

    # Restart sshd only when there is something to pick up (config rewritten)
    # or the service is not Running. The v4 unconditional restart made the
    # 10-minute SYSTEM repair watch drop healthy long-lived SSH sessions.
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


# ---------- host.tasks.list / detail / clean ----------

_TASK_SUMMARY_KEYS = ("name", "enabled", "state", "lastRunTime", "lastTaskResult", "nextRunTime")


def _tasks_list(payload: dict[str, Any]) -> dict[str, Any]:
    """Summary by default (X570's full listing is context poison); verbose=true
    adds actions/principal XML. limit + truncated semantics like file.list."""
    prefix = payload.get("prefix")
    if prefix is None:
        prefix = TASK_PREFIX
    names = payload.get("taskNames") or []
    verbose = bool(payload.get("verbose"))
    limit = payload.get("limit")
    older_than_days = payload.get("olderThanDays")
    items = tasks_mod.list_tasks(prefix=prefix, names=names)
    if older_than_days is not None:
        items = tasks_mod.filter_tasks_for_clean(
            items, prefix="", older_than_days=int(older_than_days),
            include_never_run=True, keep_names=())
    truncated = False
    if isinstance(limit, int) and limit > 0 and len(items) > limit:
        items = items[:limit]
        truncated = True
    if not verbose:
        items = [{k: t.get(k) for k in _TASK_SUMMARY_KEYS} for t in items]
    return {
        "data": {"tasks": items, "count": len(items), "truncated": truncated,
                 "verbose": verbose},
        "stdout_text": _json_compact({"tasks": items}) + "\n",
        "evidence_extra": ["tasks.list:" + str(prefix)],
    }


def _tasks_clean(payload: dict[str, Any]) -> dict[str, Any]:
    """Bulk-clean stale managed tasks (the X570 ~180 one-shot junk problem).

    Safety: dryRun defaults TRUE. Matching needs BOTH the prefix and the age
    condition. Infra tasks (repair trio / apply agent / StreamServ) are kept
    unless keepNames is explicitly overridden. Deletion still goes through the
    SYSTEM path like every other task write.
    """
    prefix = payload.get("prefix")
    if prefix is None:
        prefix = TASK_PREFIX
    older_than_days = int(payload.get("olderThanDays") if payload.get("olderThanDays") is not None else 30)
    dry_run = bool(payload.get("dryRun", True))
    include_never_run = bool(payload.get("includeNeverRun", False))
    limit = payload.get("limit")
    keep_names = payload.get("keepNames")
    if keep_names is None:
        keep_names = list(REPAIR_TASK_NAMES) + [APPLY_TASK_NAME, f"{TASK_PREFIX} StreamServ Start"]

    items = tasks_mod.list_tasks(prefix=prefix, names=[])
    matched = tasks_mod.filter_tasks_for_clean(
        items, prefix=prefix, older_than_days=older_than_days,
        include_never_run=include_never_run, keep_names=tuple(keep_names))
    truncated = False
    if isinstance(limit, int) and limit > 0 and len(matched) > limit:
        matched = matched[:limit]
        truncated = True

    deleted: list[str] = []
    errors: list[dict[str, str]] = []
    if not dry_run:
        for task in matched:
            result = tasks_mod.delete_task(str(task.get("name") or ""))
            if result.get("deleted"):
                deleted.append(str(task.get("name")))
            else:
                errors.append({"name": str(task.get("name")),
                               "error": str(result.get("error") or "unknown")})
    data = {
        "dryRun": dry_run,
        "prefix": prefix,
        "olderThanDays": older_than_days,
        "includeNeverRun": include_never_run,
        "keepNames": keep_names,
        "matched": [{k: t.get(k) for k in _TASK_SUMMARY_KEYS} for t in matched],
        "matchedCount": len(matched),
        "truncated": truncated,
        "deleted": deleted,
        "errors": errors,
    }
    return {
        "data": data,
        "stdout_text": _json_compact(data) + "\n",
        "evidence_extra": ["tasks.clean" + (":dry" if dry_run else "")],
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
    """Trigger the SYSTEM `<TASK_PREFIX> WRE Apply` agent to re-register all
    managed tasks. Requires that agent to have been installed once (elevated).
    No elevation needed to trigger it."""
    result = tasks_mod.run_task(APPLY_TASK_NAME)
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
    ("host.info", _info),
    ("host.probe", _probe),
    ("host.guard", _guard),
    ("host.repair", _repair),
    ("host.policy", _policy),
    ("host.tasks.list", _tasks_list),
    ("host.tasks.detail", _tasks_detail),
    ("host.tasks.clean", _tasks_clean),
    ("host.task.create", _task_create),
    ("host.task.update", _task_update),
    ("host.task.run", _task_run),
    ("host.task.delete", _task_delete),
    ("host.tasks.apply", _tasks_apply),
)
