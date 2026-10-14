"""Windows service control via pywin32 — no `sc.exe`, no `schtasks.exe`.

Hard dependency on pywin32 on the Windows host. Wrapped so that probe paths
that only read state do not crash if the import is unavailable.

v5: service names are validated against a whitelist before being interpolated
into the WQL query (defense in depth; current callers only pass constants).
"""

from __future__ import annotations

import re
import time
from typing import Any

_SERVICE_NAME_RE = re.compile(r"^[A-Za-z0-9_ -]+$")


# crap:ok windows-only lane (COM/service/sshd/sftp/interactive) — verified on-host by scripts/verify_v6_remote.py; off-Windows coverage N/A
def _validate_service_name(name: str) -> str:
    if not _SERVICE_NAME_RE.match(name or ""):
        raise ValueError(f"invalid service name: {name!r}")
    return name


def _import_win32serviceutil():  # type: ignore[no-untyped-def]
    import win32serviceutil  # type: ignore
    import win32service  # type: ignore
    return win32serviceutil, win32service


# crap:ok windows-only lane (COM/service/sshd/sftp/interactive) — verified on-host by scripts/verify_v6_remote.py; off-Windows coverage N/A
def service_state_safe(name: str) -> dict[str, Any]:
    try:
        _validate_service_name(name)
        win32serviceutil, win32service = _import_win32serviceutil()
        from win32com.client import GetObject  # type: ignore
        wmi = GetObject(r"winmgmts:root\cimv2")
        for service in wmi.ExecQuery(f"SELECT * FROM Win32_Service WHERE Name = '{name}'"):
            return {
                "name": name,
                "state": str(service.State),
                "startMode": str(service.StartMode),
                "startName": str(service.StartName),
                "status": str(service.Status),
                "processId": int(service.ProcessId) if service.ProcessId else 0,
            }
    except Exception as exc:  # noqa: BLE001
        return {"name": name, "error": str(exc), "state": "unknown"}
    return {"name": name, "state": "absent", "error": "service not found"}


# crap:ok windows-only lane (COM/service/sshd/sftp/interactive) — verified on-host by scripts/verify_v6_remote.py; off-Windows coverage N/A
def restart_service_safe(name: str, timeout_seconds: int = 20) -> dict[str, Any]:
    _validate_service_name(name)
    try:
        win32serviceutil, win32service = _import_win32serviceutil()
        deadline = time.monotonic() + timeout_seconds
        state = win32serviceutil.QueryServiceStatus(name)[1]
        was_stopped = state == win32service.SERVICE_STOPPED

        def wait_for(desired: int, phase: str) -> None:
            while win32serviceutil.QueryServiceStatus(name)[1] != desired:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"{name} did not finish {phase} within {timeout_seconds}s")
                time.sleep(0.2)

        if not was_stopped:
            if state != win32service.SERVICE_STOP_PENDING:
                win32serviceutil.StopService(name)
            wait_for(win32service.SERVICE_STOPPED, "stopping")
        win32serviceutil.StartService(name)
        wait_for(win32service.SERVICE_RUNNING, "starting")
        return {"name": name, "action": "started" if was_stopped else "restarted",
                "deadlineSeconds": timeout_seconds}
    except Exception as exc:  # noqa: BLE001
        return {"name": name, "action": "restart.error", "error": str(exc)}


def ensure_auto_recovery_safe(name: str) -> dict[str, Any]:
    """Delay automatic startup and retry failed starts after the VPN can settle."""
    _validate_service_name(name)
    try:
        _, win32service = _import_win32serviceutil()
        manager = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
        try:
            access = (win32service.SERVICE_QUERY_CONFIG | win32service.SERVICE_CHANGE_CONFIG
                      | win32service.SERVICE_START)
            handle = win32service.OpenService(manager, name, access)
            try:
                changed: list[str] = []
                if win32service.QueryServiceConfig(handle)[1] != win32service.SERVICE_AUTO_START:
                    win32service.ChangeServiceConfig(
                        handle, win32service.SERVICE_NO_CHANGE, win32service.SERVICE_AUTO_START,
                        win32service.SERVICE_NO_CHANGE, None, None, 0, None, None, None, None,
                    )
                    changed.append("automatic")
                if not win32service.QueryServiceConfig2(handle, win32service.SERVICE_CONFIG_DELAYED_AUTO_START_INFO):
                    win32service.ChangeServiceConfig2(handle, win32service.SERVICE_CONFIG_DELAYED_AUTO_START_INFO, True)
                    changed.append("delayedAutoStart")
                recovery = win32service.QueryServiceConfig2(handle, win32service.SERVICE_CONFIG_FAILURE_ACTIONS)
                desired_actions = [(win32service.SC_ACTION_RESTART, 60000)] * 3
                if recovery["ResetPeriod"] != 86400 or list(recovery["Actions"]) != desired_actions:
                    recovery.update(ResetPeriod=86400, Actions=desired_actions)
                    win32service.ChangeServiceConfig2(handle, win32service.SERVICE_CONFIG_FAILURE_ACTIONS, recovery)
                    changed.append("failureActions")
                if not win32service.QueryServiceConfig2(handle, win32service.SERVICE_CONFIG_FAILURE_ACTIONS_FLAG):
                    win32service.ChangeServiceConfig2(handle, win32service.SERVICE_CONFIG_FAILURE_ACTIONS_FLAG, True)
                    changed.append("failureActionsFlag")
                return {"name": name, "ensured": True, "changed": changed,
                        "delayedAutoStart": True, "restartDelaySeconds": 60}
            finally:
                win32service.CloseServiceHandle(handle)
        finally:
            win32service.CloseServiceHandle(manager)
    except Exception as exc:  # noqa: BLE001
        return {"name": name, "ensured": False, "error": str(exc)}


def set_service_start_mode_safe(name: str, mode: str) -> dict[str, Any]:
    """Set start mode: 'auto' | 'demand' | 'disabled'."""
    _validate_service_name(name)
    try:
        win32serviceutil, win32service = _import_win32serviceutil()
        desired = {
            "auto": win32service.SERVICE_AUTO_START,
            "demand": win32service.SERVICE_DEMAND_START,
            "disabled": win32service.SERVICE_DISABLED,
        }[mode]
        import win32con  # type: ignore
        hscm = win32service.OpenSCManager(None, None, win32con.SC_MANAGER_CONNECT)
        try:
            hservice = win32service.OpenService(hscm, name, win32con.SERVICE_CHANGE_CONFIG)
            try:
                win32service.ChangeServiceConfig(
                    hservice, win32service.SERVICE_NO_CHANGE, desired,
                    win32service.SERVICE_NO_CHANGE, None, None, 0, None, None, None, None,
                )
                return {"name": name, "startMode": mode, "set": True}
            finally:
                win32service.CloseServiceHandle(hservice)
        finally:
                win32service.CloseServiceHandle(hscm)
    except Exception as exc:  # noqa: BLE001
        return {"name": name, "startMode": mode, "set": False, "error": str(exc)}
