"""Self-description for the v6 action surface: JSON Schemas + help text.

`host.capabilities` embeds PAYLOAD_SCHEMAS so an agent can validate a payload
before sending it; `system.help` returns the HELP entry (human + machine
readable, with an example) for one action or all of them.

Keep entries terse — they travel in every capabilities response. Schemas are
draft-07-ish dicts; only the keywords an agent needs (type/required/
properties/items/enum/default) are used.
"""

from __future__ import annotations

from typing import Any

_STR = {"type": "string"}
_INT = {"type": "integer"}
_BOOL = {"type": "boolean"}
_STRS = {"type": "array", "items": {"type": "string"}}

_PROCESS_BASE: dict[str, Any] = {
    "exe": {**_STR, "description": "absolute path; no PATH search (anti PATH-hijack)"},
    "args": {**_STRS, "description": "argv array — NEVER a shell string"},
    "cwd": _STR,
    "stdinText": _STR,
    "env": {"type": "object", "additionalProperties": {"type": "string"}},
    "timeoutMs": {**_INT, "description": "server-enforced; kill on expiry"},
    "captureKB": {**_INT, "default": 256, "description": "ring-buffer size per stream"},
}

PAYLOAD_SCHEMAS: dict[str, dict[str, Any]] = {
    "host.capabilities": {"type": "object", "properties": {}},
    "host.info": {"type": "object", "properties": {}},
    "system.help": {"type": "object", "properties": {"action": _STR}},
    "host.probe": {"type": "object", "properties": {
        "categories": {**_STRS, "description": "subset of os,sshd,policy,tasks,python"}}},
    "host.guard": {"type": "object", "properties": {
        "noDisable": _BOOL, "expectedListenAddress": _STR, "logPath": _STR}},
    "host.repair": {"type": "object", "properties": {
        "expectedListenAddress": _STR, "forceRewrite": _BOOL, "logPath": _STR}},
    "host.policy": {"type": "object", "properties": {
        "token": _STR, "disableToken": _BOOL, "expectedListenAddress": _STR,
        "exposureMode": {"type": "string", "enum": ["private-only", "public-with-token"]},
        "label": _STR}},
    "host.tasks.list": {"type": "object", "properties": {
        "prefix": _STR, "taskNames": _STRS, "verbose": _BOOL, "limit": _INT,
        "olderThanDays": _INT}},
    "host.tasks.detail": {"type": "object", "required": ["name"],
                          "properties": {"name": _STR}},
    "host.tasks.clean": {"type": "object", "properties": {
        "prefix": _STR, "olderThanDays": _INT, "dryRun": {**_BOOL, "default": True},
        "includeNeverRun": {**_BOOL, "default": False}, "keepNames": _STRS, "limit": _INT}},
    "host.tasks.apply": {"type": "object", "properties": {}},
    "host.task.create": {"type": "object", "required": ["name", "exe"], "properties": {
        "name": _STR, "exe": _STR, "args": _STRS, "cwd": _STR,
        "trigger": {"type": "string", "enum": ["manual", "time", "logon", "startup", "boot", "interval"]},
        "delay_iso": _STR, "interval_minutes": _INT,
        "run_as_user": {**_STR, "description": "default SYSTEM; '' = current SSH user (S4U)"},
        "run_level": {"type": "integer", "enum": [0, 1]}, "enabled": _BOOL,
        "description": _STR,
        "deleteAfterRun": {**_BOOL, "description": "auto-delete at expiresAt (default +24h)"},
        "expiresAt": {**_STR, "description": "ISO-8601; EndBoundary for deleteAfterRun"}}},
    "host.task.update": {"type": "object", "required": ["name"], "properties": {
        "name": _STR, "exe": _STR, "args": _STRS, "cwd": _STR, "trigger": _STR,
        "enabled": _BOOL, "description": _STR, "run_as_user": _STR,
        "run_level": _INT, "logon_type": _INT}},
    "host.task.run": {"type": "object", "required": ["name"], "properties": {"name": _STR}},
    "host.task.delete": {"type": "object", "required": ["name"], "properties": {"name": _STR}},
    "file.writeText": {"type": "object", "required": ["path", "text"], "properties": {
        "path": _STR, "text": _STR, "allowProtected": _BOOL}},
    "file.readText": {"type": "object", "required": ["path"], "properties": {
        "path": _STR, "maxBytes": _INT, "offset": {**_INT, "description": "byte offset"},
        "tail": {**_INT, "description": "last N bytes"},
        "includeBase64": _BOOL, "includeProof": _BOOL}},
    "file.mkdir": {"type": "object", "required": ["path"], "properties": {
        "path": _STR, "allowProtected": _BOOL}},
    "file.deleteTree": {"type": "object", "required": ["path"], "properties": {
        "path": _STR, "allowProtected": _BOOL}},
    "file.copy": {"type": "object", "required": ["source", "destination"], "properties": {
        "source": _STR, "destination": _STR, "allowProtected": _BOOL}},
    "file.putBinary": {"type": "object", "required": ["path", "base64"], "properties": {
        "path": _STR, "base64": {**_STR, "description": "<= 32 MB decoded per file"},
        "chunkIndex": _INT, "totalChunks": _INT, "allowProtected": _BOOL}},
    "file.list": {"type": "object", "required": ["path"], "properties": {
        "path": _STR, "pattern": _STR, "recursive": _BOOL, "maxEntries": _INT}},
    "file.search": {"type": "object", "required": ["root"], "properties": {
        "root": _STR, "nameGlob": _STR, "contentRegex": _STR,
        "maxMatches": _INT, "maxFileBytes": _INT}},
    "process.run": {"type": "object", "required": ["exe"], "properties": dict(_PROCESS_BASE)},
    "process.start": {"type": "object", "required": ["exe"],
                      "properties": {**_PROCESS_BASE,
                                     "retentionHours": {**_INT, "default": 24}}},
    "process.wait": {"type": "object", "required": ["jobId"], "properties": {
        "jobId": _STR, "timeoutMs": _INT, "tailKB": _INT}},
    "process.status": {"type": "object", "required": ["jobId"], "properties": {"jobId": _STR}},
    "process.kill": {"type": "object", "required": ["jobId"], "properties": {
        "jobId": _STR, "force": {**_BOOL, "description": "force = kill process tree"}}},
    "wsl.run": {"type": "object", "properties": {
        "distro": _STR, "user": _STR,
        "argv": {**_STRS, "description": "preferred: wsl --exec passthrough, no shell"},
        "shell": {**_STR, "description": "alternative: bash -lc <string> (xor with argv); ';'-joined commands report only the last exit status"},
        "winCwd": {**_STR, "description": "translated to wsl --cd"},
        "timeoutMs": {**_INT, "default": 60000}, "captureKB": _INT,
        "env": {"type": "object", "additionalProperties": {"type": "string"}}}},
    "wsl.list": {"type": "object", "properties": {}},
    "wsl.wslpath": {"type": "object", "required": ["path", "to"], "properties": {
        "path": _STR, "to": {"type": "string", "enum": ["u", "w"]}}},
    "wsl.status": {"type": "object", "properties": {}},
}

# identity: "ssh-user" (non-elevated logon context) or "system-path"
# (TaskScheduler COM / protected paths; the documented token==SYSTEM lane).
HELP: dict[str, dict[str, Any]] = {
    "host.capabilities": {"summary": "Protocol version, build tag, action list, per-action JSON Schemas.", "identity": "ssh-user", "example": {}},
    "host.info": {"summary": "Cheap pre-flight self-check: whoami, protocolVersion, build, policy label/status, uptime, wreRoot, taskPrefix.", "identity": "ssh-user", "example": {}},
    "system.help": {"summary": "Human+machine docs for one action (or all when payload.action is omitted).", "identity": "ssh-user", "example": {"action": "process.run"}},
    "host.probe": {"summary": "Host snapshot: os / sshd / policy / tasks / python categories.", "identity": "ssh-user", "example": {"categories": ["os", "sshd", "policy"]}},
    "host.guard": {"summary": "sshd exposure diagnostics. noDisable=true = read-only (never touches the service).", "identity": "ssh-user", "example": {"noDisable": True}},
    "host.repair": {"summary": "Rewrite sshd_config when drifted, restart sshd only when needed, ensure repair tasks + firewall rule.", "identity": "system-path", "example": {}},
    "host.policy": {"summary": "Write access-policy.json. Token given -> hash+install; omitted -> preserve existing hash.", "identity": "system-path", "example": {"label": "PRIVATE-ONLY"}},
    "host.tasks.list": {"summary": "Summary list by default (name/state/lastResult/nextRunTime); verbose=true adds actions/principal. limit+truncated semantics.", "identity": "ssh-user", "example": {"prefix": "WRE", "limit": 50}},
    "host.tasks.detail": {"summary": "Full definition incl. XML for one task.", "identity": "ssh-user", "example": {"name": "WRE WRE Apply"}},
    "host.tasks.clean": {"summary": "Bulk-clean stale managed tasks. dryRun defaults TRUE; prefix+olderThanDays double condition; infra tasks kept unless keepNames overridden.", "identity": "system-path", "example": {"prefix": "WRE", "olderThanDays": 30, "dryRun": True}},
    "host.tasks.apply": {"summary": "Trigger the SYSTEM apply-agent task to re-register managed tasks.", "identity": "system-path", "example": {}},
    "host.task.create": {"summary": "Register a scheduled task via COM. deleteAfterRun=true auto-deletes at expiresAt (default +24h). SYSTEM by default.", "identity": "system-path", "example": {"name": "WRE My Job", "exe": "C:/Windows/System32/cmd.exe", "args": ["/d", "/c", "echo hi"], "trigger": "manual", "deleteAfterRun": True}},
    "host.task.update": {"summary": "Read-modify-write update; absent fields keep existing values.", "identity": "system-path", "example": {"name": "X", "enabled": False}},
    "host.task.run": {"summary": "Trigger a task now.", "identity": "system-path", "example": {"name": "WRE My Job"}},
    "host.task.delete": {"summary": "Delete one task by name.", "identity": "system-path", "example": {"name": "WRE My Job"}},
    "file.writeText": {"summary": "Atomic UTF-8 text write (tmp+replace; UNC targets fall back to direct write). //wsl.localhost/<distro>/... UNC supported. Returns sha256 proof.", "identity": "ssh-user", "example": {"path": "C:/WRE/inbox/a.txt", "text": "hi\n"}},
    "file.readText": {"summary": "Read UTF-8 text. offset/tail (bytes) for logs; base64+proof are opt-in (includeBase64/includeProof). //wsl.localhost/... UNC supported.", "identity": "ssh-user", "example": {"path": "C:/WRE/jobs/j-abc.log", "tail": 4000}},
    "file.mkdir": {"summary": "mkdir -p.", "identity": "ssh-user", "example": {"path": "C:/WRE/inbox/d"}},
    "file.deleteTree": {"summary": "Delete file or directory tree.", "identity": "ssh-user", "example": {"path": "C:/WRE/inbox/d"}},
    "file.copy": {"summary": "Copy one file (metadata preserved).", "identity": "ssh-user", "example": {"source": "C:/a.txt", "destination": "C:/b.txt"}},
    "file.putBinary": {"summary": "Base64 write, <=32 MB decoded; chunkIndex/totalChunks for chunked upload. //wsl.localhost/... UNC supported. Larger -> SFTP.", "identity": "ssh-user", "example": {"path": "C:/WRE/inbox/f.bin", "base64": "AAE=", "chunkIndex": 0, "totalChunks": 1}},
    "file.list": {"summary": "Directory listing with pattern/recursive/maxEntries.", "identity": "ssh-user", "example": {"path": "C:/WRE/inbox"}},
    "file.search": {"summary": "Name-glob and/or content-regex search under root.", "identity": "ssh-user", "example": {"root": "C:/WRE/logs", "nameGlob": "*.log"}},
    "process.run": {"summary": "Run exe synchronously; returns exitCode/stdout/stderr/durationMs/timedOut/pid. args MUST be an array; server-side timeout kills the process. cmd /c available via exe=cmd.exe args=[/d,/c,...] at your own risk.", "identity": "ssh-user", "example": {"exe": "C:/Windows/System32/cmd.exe", "args": ["/d", "/c", "echo hi"], "timeoutMs": 30000}},
    "process.start": {"summary": "Start async job -> {jobId, outputPath}. Output ring-buffered to WRE_ROOT/jobs/<id>.log; metadata in <id>.json; reaped across rpc restarts; 24h retention. job exitCode is the spawned exe's real exit code; for shell composites (bash -lc 'a; b') that is the LAST command's status only — jobs spawned via a shell wrapper carry meta.exitCodeNote explaining the semantics (use 'set -e' or 'cmd; rc=$?; exit $rc').", "identity": "ssh-user", "example": {"exe": "C:/WRE/wre/python/python.exe", "args": ["-c", "import time;time.sleep(60)"]}},
    "process.wait": {"summary": "Long-poll a job until exit or timeoutMs; returns exitCode + log tail.", "identity": "ssh-user", "example": {"jobId": "j-abc", "timeoutMs": 60000}},
    "process.status": {"summary": "Job state: running/exited, durationMs, exitCode, outputSizeBytes.", "identity": "ssh-user", "example": {"jobId": "j-abc"}},
    "process.kill": {"summary": "Terminate a job; force=true kills the process tree (taskkill /T /F).", "identity": "ssh-user", "example": {"jobId": "j-abc", "force": False}},
    "wsl.run": {"summary": "Run in WSL via wsl.exe. argv -> --exec passthrough (no shell); shell -> bash -lc (';'-joined commands report only the last command's exit status — use 'set -e' or 'cmd; rc=$?; exit $rc'). WSL_UTF8=1 injected; default timeout 60s. SSH-user context only — SYSTEM cannot see user distros. LIFECYCLE: each wsl.exe call cold-boots a fresh distro session and WSL kills ALL its user processes at exit (setsid/nohup/tmux do NOT survive) — long-lived WSL services must run as ONE process.start job: process.start {exe:'C:/Windows/System32/wsl.exe', args:['-d','<distro>','--exec','bash','-lc','exec your-server']}.", "identity": "ssh-user", "example": {"distro": "Ubuntu", "argv": ["/usr/bin/python3", "--version"]}},
    "wsl.list": {"summary": "Parse wsl.exe -l -v -> distros with state/default/wslVersion.", "identity": "ssh-user", "example": {}},
    "wsl.wslpath": {"summary": "Translate a path Windows<->Linux (to: 'u'|'w').", "identity": "ssh-user", "example": {"path": "C:/work", "to": "u"}},
    "wsl.status": {"summary": "WSL availability + whether any distro is Running (doubles as VM warm-up).", "identity": "ssh-user", "example": {}},
}
