"""Scheduled task management via TaskScheduler COM — `win32com.client.Dispatch`.

No `schtasks.exe`, no PowerShell. Pure IDispatch through pywin32 (already
shipped in the embeddable Python, see make_bootstrap_package.py).
"""

from __future__ import annotations

import os
from typing import Any


# Task Scheduler magic constants from taskschd.h
TASK_ACTION_EXEC = 0
TASK_TRIGGER_TIME = 1
TASK_TRIGGER_DAILY = 2
TASK_TRIGGER_WEEKLY = 3
TASK_TRIGGER_MONTHLY = 4
TASK_TRIGGER_MONTHLYDOW = 5
TASK_TRIGGER_IDLE = 6
TASK_TRIGGER_REGISTRATION = 7
TASK_TRIGGER_BOOT = 8
TASK_TRIGGER_LOGON = 9
TASK_TRIGGER_SESSION_STATE_CHANGE = 11

TASK_LOGON_NONE = 0
TASK_LOGON_PASSWORD = 1
TASK_LOGON_S4U = 2
TASK_LOGON_INTERACTIVE_TOKEN = 3
TASK_LOGON_GROUP = 4
TASK_LOGON_SERVICE_ACCOUNT = 5
TASK_LOGON_INTERACTIVE_TOKEN_OR_PASSWORD = 6
# SSH sessions on Windows come in WITHOUT an interactive token, so the only
# "current user, no password" logon type that RegisterTaskDefinition accepts
# when NOT elevated is S4U. INTERACTIVE_TOKEN requires the caller's interactive
# logon, which doesn't exist over SSH. GROUP also works without a password but
# is meant for group-scoped SIDs.
TASK_LOGON_DEFAULT_CURRENT_USER = TASK_LOGON_S4U

TASK_RUNLEVEL_LUA = 0
TASK_RUNLEVEL_HIGHEST = 1

TASK_CREATE_OR_UPDATE = 6
TASK_FLAG_DEFAULT = 0

TRIGGER_NAME_TO_ID = {
    "manual": TASK_TRIGGER_TIME,  # enabled but not auto-running
    "time": TASK_TRIGGER_TIME,
    "logon": TASK_TRIGGER_LOGON,
    "startup": TASK_TRIGGER_BOOT,
    "boot": TASK_TRIGGER_BOOT,
    "interval": TASK_TRIGGER_TIME,
}

TRIGGER_KINDS = tuple(TRIGGER_NAME_TO_ID.keys())


def _win_path(value: str) -> str:
    if not value:
        return value
    if len(value) >= 3 and value[1] == ":" and value[2] == "/":
        return value.replace("/", "\\")
    return value


def _win_args(args: list[str]) -> list[str]:
    return [_win_path(str(arg)) for arg in args]


def _import_disp():  # type: ignore[no-untyped-def]
    import pythoncom  # type: ignore
    import win32com.client  # type: ignore
    pythoncom.CoInitialize()
    return win32com.client, pythoncom


def _connect_task_service():  # type: ignore[no-untyped-def]
    win32com, _pythoncom = _import_disp()
    ts = win32com.Dispatch("Schedule.Service")
    # Empty server name connects to the local Task Scheduler service.
    ts.Connect("", "", "", "")
    return ts


def _root_folder(ts):  # type: ignore[no-untyped-def]
    return ts.GetFolder("\\")


def list_tasks(*, prefix: str = "CodexRemote", names: list[str] | None = None) -> list[dict[str, Any]]:
    try:
        ts = _connect_task_service()
        folder = _root_folder(ts)
        items: list[dict[str, Any]] = []
        names_set = {n or "" for n in (names or [])}
        collection = folder.GetTasks(TASK_FLAG_DEFAULT)
        # collection is an IRegisteredTaskCollection; iterate by index.
        count = int(collection.Count)
        for i in range(1, count + 1):
            task = collection.Item(i)
            name = str(task.Name)
            if names_set and name not in names_set:
                continue
            if prefix and not name.startswith(prefix):
                continue
            items.append(_extract_task_summary(task))
        return items
    except Exception as exc:  # noqa: BLE001
        return [{"error": str(exc)}]


def list_repair_tasks(task_names: tuple[str, ...]) -> list[dict[str, Any]]:
    return list_tasks(prefix="", names=list(task_names))


def detail_task(name: str) -> dict[str, Any]:
    try:
        ts = _connect_task_service()
        folder = _root_folder(ts)
        task = folder.GetTask(name)
        return _extract_task_detail(task)
    except Exception as exc:  # noqa: BLE001
        return {"name": name, "error": str(exc)}


def run_task(name: str) -> dict[str, Any]:
    """Trigger IRegisteredTask.Run with no parameters."""
    try:
        ts = _connect_task_service()
        folder = _root_folder(ts)
        task = folder.GetTask(name)
        task.Run("")
        return {"name": name, "run": True}
    except Exception as exc:  # noqa: BLE001
        return {"name": name, "run": False, "error": str(exc)}


def resolve_run_as_user(value: str | None) -> str:
    """Resolve CURRENT/. sentinel to the current Windows token user."""
    raw = "" if value is None else str(value).strip()
    if raw.upper() in ("CURRENT", "<CURRENT>", "."):
        return _current_full_user_name()
    return raw


def create_task(spec: dict[str, Any]) -> dict[str, Any]:
    """Register a new task with one ExecAction via TaskScheduler COM.

    `spec`:
      name (required)
      exe  (required) — Windows path, e.g. C:/Windows/System32/cmd.exe
      args (list[str]) — argv joined as TaskScheduler ExecAction.Arguments
      cwd (string)
      trigger (manual|logon|startup|boot|interval; default manual)
      trigger_params (dict, e.g. {"delay":"PT30S","repetitionMinutes":10})
      delay_iso (e.g. "PT60S") — convenient shortcut
      interval_minutes (int) — shortcut for interval repetition
      run_as_user — defaults to "SYSTEM"
      run_level — 0 (limited) | 1 (highest); defaults 1
      logon_type — see TASK_LOGON_* above; auto-picks SYSTEM / interactive
      enabled (bool; default True)
      description (str; defaults to name)
    """
    name = str(spec.get("name") or "").strip()
    if not name:
        raise ValueError("host.task.create requires payload.name")
    exe = spec.get("exe")
    if not exe:
        raise ValueError("host.task.create requires payload.exe")
    exe = _win_path(str(exe))
    args = _win_args(list(spec.get("args") or []))
    cwd = _win_path(str(spec.get("cwd") or ""))
    trigger_kind = (spec.get("trigger") or "manual").lower()
    if trigger_kind not in TRIGGER_KINDS:
        raise ValueError(f"trigger must be one of {TRIGGER_KINDS}, got {trigger_kind}")
    delay_iso = spec.get("delay_iso") or spec.get("delay")
    interval_minutes = spec.get("interval_minutes")
    run_as_user = resolve_run_as_user(spec.get("run_as_user") or "SYSTEM")
    run_level = int(spec.get("run_level") if spec.get("run_level") is not None else TASK_RUNLEVEL_HIGHEST)
    enabled = bool(spec.get("enabled", True))
    description = spec.get("description") or name

    if run_as_user.lower() == "system":
        logon_type = TASK_LOGON_SERVICE_ACCOUNT
        register_user = "SYSTEM"
        register_password = None
        register_logon_flag = TASK_LOGON_SERVICE_ACCOUNT
    elif run_as_user == "":
        # Implicit "current SSH/logon user" — pick S4U (no password needed;
        # works in non-elevated SSH sessions). RegisterTaskDefinition with S4U
        # requires an explicit DOMAIN\USER, otherwise it returns
        # ERROR_LOGON_FAILURE (0x8007052E masked as DISP_E_EXCEPTION).
        logon_type = int(spec.get("logon_type", TASK_LOGON_DEFAULT_CURRENT_USER))
        register_user = _current_full_user_name()
        register_password = None
        register_logon_flag = TASK_LOGON_DEFAULT_CURRENT_USER
    else:
        # Explicit user without password: use S4U by default. INTERACTIVE_TOKEN
        # requires an already-available interactive desktop token and fails when
        # the SYSTEM apply-agent registers tasks on behalf of that user.
        logon_type = int(spec.get("logon_type", TASK_LOGON_S4U))
        register_user = run_as_user
        register_password = None
        register_logon_flag = logon_type

    ts = _connect_task_service()
    folder = _root_folder(ts)
    definition = ts.NewTask(TASK_FLAG_DEFAULT)
    definition.RegistrationInfo.Description = description
    # Action
    action = definition.Actions.Create(TASK_ACTION_EXEC)
    action.Path = exe
    action.Arguments = " ".join(args)
    if cwd:
        action.WorkingDirectory = cwd

    # Trigger (manual = no trigger; just registered as runnable)
    if trigger_kind != "manual":
        trigger_id = TRIGGER_NAME_TO_ID[trigger_kind]
        trigger = definition.Triggers.Create(trigger_id)
        if delay_iso:
            try:
                trigger.Delay = delay_iso
            except Exception:  # noqa: BLE001
                pass
        if interval_minutes:
            try:
                trigger.Repetition.Interval = f"PT{int(interval_minutes)}M"
            except Exception:  # noqa: BLE001
                pass

    # Principal
    principal = definition.Principal
    if run_as_user:
        principal.UserId = run_as_user
    principal.LogonType = logon_type
    principal.RunLevel = run_level

    # Settings
    definition.Settings.Enabled = enabled
    definition.Settings.StartWhenAvailable = True
    definition.Settings.StopIfGoingOnBatteries = False
    definition.Settings.DisallowStartIfOnBatteries = False

    # TASK_CREATE_OR_UPDATE overwrites safely. Do NOT delete first: if Windows
    # rejects the new principal (common for user tasks from a SYSTEM apply-agent),
    # deleting first would remove the last known-good task.
    folder.RegisterTaskDefinition(
        name, definition, TASK_CREATE_OR_UPDATE,
        register_user, register_password, register_logon_flag, None,
    )
    return {
        "name": name,
        "created": True,
        "exe": exe,
        "args": args,
        "cwd": cwd,
        "trigger": trigger_kind,
        "runAsUser": run_as_user or "<current>",
    }


def update_task(spec: dict[str, Any]) -> dict[str, Any]:
    result = create_task(spec)
    result["updated"] = True
    return result


def delete_task(name: str) -> dict[str, Any]:
    try:
        ts = _connect_task_service()
        folder = _root_folder(ts)
        _try_delete(folder, name)
        return {"name": name, "deleted": True}
    except Exception as exc:  # noqa: BLE001
        return {"name": name, "deleted": False, "error": str(exc)}


def _extract_task_summary(task) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    actions_info: list[dict[str, Any]] = []
    try:
        actions = task.Definition.Actions
        count = int(actions.Count)
        for i in range(1, count + 1):
            action = actions.Item(i)
            actions_info.append({
                "kind": int(action.Type),
                "exe": str(getattr(action, "Path", "") or ""),
                "args": str(getattr(action, "Arguments", "") or ""),
                "workingDirectory": str(getattr(action, "WorkingDirectory", "") or ""),
            })
    except Exception:  # noqa: BLE001
        pass
    principal_info: dict[str, Any] = {}
    try:
        principal = task.Definition.Principal
        principal_info = {
            "userId": str(getattr(principal, "UserId", "") or ""),
            "logonType": int(getattr(principal, "LogonType", 0) or 0),
            "runLevel": int(getattr(principal, "RunLevel", 0) or 0),
        }
    except Exception:  # noqa: BLE001
        pass
    return {
        "name": str(task.Name),
        "path": str(task.Path),
        "enabled": bool(task.Enabled),
        "state": int(task.State),
        "lastRunTime": _iso(task.LastRunTime),
        "lastTaskResult": int(task.LastTaskResult) if task.LastTaskResult else 0,
        "nextRunTime": _iso(task.NextRunTime),
        "principal": principal_info,
        "actions": actions_info,
    }


def _extract_task_detail(task) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    summary = _extract_task_summary(task)
    try:
        summary["definitionXml"] = task.Definition.XmlText or ""
    except Exception:  # noqa: BLE001
        pass
    summary["registeredTaskXml"] = task.XmlText or ""
    return summary


def _iso(value) -> str:  # type: ignore[no-untyped-def]
    if value is None:
        return ""
    try:
        # COM dates come as pywin32 Time objects; isoformat is fine.
        return value.isoformat()
    except Exception:  # noqa: BLE001
        try:
            return str(value)
        except Exception:
            return ""


def ensure_repair_tasks(*, expected_listen: str | None) -> dict[str, Any]:
    """Re-create the three CodexRemote Sshd Repair tasks."""
    pythonw = "C:/CodexRemote/wre/python/pythonw.exe"
    wre_entry = "C:/CodexRemote/wre/rpc.py"

    task_defs: list[dict[str, Any]] = [
        {"name": "CodexRemote Sshd Repair Logon", "trigger": "logon", "delay_iso": "PT30S"},
        {"name": "CodexRemote Sshd Repair Startup", "trigger": "startup", "delay_iso": "PT60S"},
        {"name": "CodexRemote Sshd Repair Watch", "trigger": "boot",
         "delay_iso": "PT2M", "interval_minutes": 10},
    ]

    results: dict[str, Any] = {"created": [], "errors": []}
    for spec in task_defs:
        try:
            create_task({
                "name": spec["name"],
                "exe": pythonw,
                "args": ["-I", "-X", "utf8", wre_entry, "repair-sshd"],
                "trigger": spec["trigger"],
                "delay_iso": spec.get("delay_iso"),
                "interval_minutes": spec.get("interval_minutes"),
                "run_as_user": "SYSTEM",
                "run_level": TASK_RUNLEVEL_HIGHEST,
                "description": "WRE v4 sshd self-repair",
            })
            results["created"].append(spec["name"])
        except Exception as exc:  # noqa: BLE001
            results["errors"].append(f"{spec['name']}: {exc}")
    return results


def ensure_streamserv_task(*, root: str = "D:/StreamServ", run_as_user: str = "SYSTEM") -> dict[str, Any]:
    """Register the v4 StreamServ launcher task.

    Targets the HEADLESS backend chain (`start_streamserv_backend.cmd`), which
    kills residual managed processes and relaunches the whole stack (nginx +
    cloudflared + archiveAdmin + autoStream). This runs fine in session 0 as
    SYSTEM — unlike the visible-console `start_streamserv.bat`, which needs an
    interactive desktop and returns exit 1 under SYSTEM.

    Runtime triggering later uses `host.task.run` and needs no elevation.
    """
    root = root.rstrip("/\\")
    return create_task({
        "name": "CodexRemote StreamServ Start",
        "exe": "C:/Windows/System32/cmd.exe",
        "args": ["/d", "/c", f"{root}/start_streamserv_backend.cmd"],
        "cwd": root,
        "trigger": "manual",
        "run_as_user": run_as_user,
        "run_level": TASK_RUNLEVEL_HIGHEST,
        "description": "Start StreamServ headless backend (full-init relaunch of nginx/cloudflared/archiveAdmin/autoStream)",
    })


def ensure_apply_agent_task(*, expected_listen: str | None = None) -> dict[str, Any]:
    """Register the SYSTEM task-registration agent `CodexRemote WRE Apply`.

    Once this SYSTEM task exists, the controller can re-register every managed
    task by triggering it (`host.task.run {name:"CodexRemote WRE Apply"}`) —
    no more elevated operator command each time v4 changes. This task itself
    must be created once from an elevated session.
    """
    pythonw = "C:/CodexRemote/wre/python/pythonw.exe"
    wre_entry = "C:/CodexRemote/wre/rpc.py"
    args = ["-I", "-X", "utf8", wre_entry, "apply-tasks"]
    if expected_listen:
        args += ["--expected-listen", expected_listen]
    return create_task({
        "name": "CodexRemote WRE Apply",
        "exe": pythonw,
        "args": args,
        "trigger": "manual",
        "run_as_user": "SYSTEM",
        "run_level": TASK_RUNLEVEL_HIGHEST,
        "description": "WRE v4 SYSTEM task-registration agent (re-applies managed tasks on demand)",
    })


def _try_delete(folder, name: str) -> None:  # type: ignore[no-untyped-def]
    try:
        folder.DeleteTask(name, 0)
    except Exception:  # noqa: BLE001
        pass


def _current_full_user_name() -> str:
    """Return `DOMAIN\\USER` for the current process token, used to fill
    RegisterTaskDefinition's user field when caller omits run_as_user."""
    import os
    try:
        import win32api  # type: ignore
        try:
            return win32api.GetDomainName() + "\\" + win32api.GetUserName()
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass
    domain = os.environ.get("USERDOMAIN") or "."
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if not user:
        return ""
    return f"{domain}\\{user}"