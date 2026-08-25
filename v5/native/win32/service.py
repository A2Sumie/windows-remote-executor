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


def _validate_service_name(name: str) -> str:
    if not _SERVICE_NAME_RE.match(name or ""):
        raise ValueError(f"invalid service name: {name!r}")
    return name


def _import_win32serviceutil():  # type: ignore[no-untyped-def]
    import win32serviceutil  # type: ignore
    import win32service  # type: ignore
    return win32serviceutil, win32service


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


def restart_service_safe(name: str, timeout_seconds: int = 20) -> dict[str, Any]:
    _validate_service_name(name)
    try:
        win32serviceutil, win32service = _import_win32serviceutil()
        win32serviceutil.StopService(name)
        # Wait until stopped.
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            state = service_state_safe(name).get("state")
            if state in ("stopped", "absent"):
                break
            time.sleep(0.5)
        win32serviceutil.StartService(name)
        return {"name": name, "action": "restarted", "deadlineSeconds": timeout_seconds}
    except Exception as exc:  # noqa: BLE001
        return {"name": name, "action": "restart.error", "error": str(exc)}


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
