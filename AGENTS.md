# Agent Guide

Use this repository to operate Windows hosts (X570, nuc-8-sumie / NUC8) from
macOS/Linux through the WRE v4 control plane.

## Source Of Truth

- v4 source tree: `v4/`
  - native entry (runs on Windows): `v4/native/rpc.py`
  - controller CLI / library: `v4/controller/`
  - bootstrap + verify scripts: `v4/scripts/`
  - protocol doc: `v4/V4.md`
  - status: deployed on X570; tree frozen — fixes land in v5, do not edit v4 in place.
- v5 audit-hardened fix tree (protocol v5): `v5/` — protocol doc `v5/V5.md`.
- v6 agent-first tree (protocol v6, process.*/wsl.*/loop mode/MCP): `v6/` — protocol doc `v6/V6.md`. **Cutover 2026-08-25:** both X570 and nuc-8-sumie serve v6.1 from the default `C:/WRE/wre` — the plain controller invocation (no `WRE_ENTRY`) is the daily route, the `WRE *` SYSTEM tasks point at the new tree, and every enabled scheduled task was migrated off the legacy tree (X570: 20 tasks rewired; backups in `C:\WRE\logs\task-backup-20260825\`). The legacy v4 tree `C:/CodexRemote/wre` is cold standby (zero enabled tasks reference it; hardcoded-protected forever); the old sidecar `C:/CodexRemote/wre6` is unreferenced and pending deletion at the 2026-08-27 cleanup.
- Legacy (kept only until X570 rolls to v4):
  - `windows-remote-executor/` — v3 Python wrapper / CLI / MCP
  - `windows-remote-executor-native/` — v3 C# native executor
- From the parent livestr workspace, run Git commands with `git -C windows-remote-executor-public ...`.

## v4 Entry Point

Fixed remote SSH command:

```
C:/CodexRemote/wre/python/python.exe -I -X utf8 C:/CodexRemote/wre/rpc.py rpc-stdio
```

stdin = one UTF-8 JSON request line. stdout = one UTF-8 JSON response line.
No PowerShell/cmd strings travel from the controller (transport constraint).
To run PowerShell or TaskScheduler COM on the host, `file.writeText` the script
and execute it via `host.task.create` (manual, `run_as_user`=SYSTEM) +
`host.task.run`. That in-host script runs as SYSTEM with full TaskScheduler COM
access, so it can create/modify any trigger type (including weekly) and kill
processes.

## First Steps

1. Read `v4/V4.md`.
2. Locate the real target env file at `windows-remote-executor/targets/<name>.env`.
3. Probe:
   ```bash
   python3 -m v4.controller.shell X570 --probe
   ```
4. For interactive use:
   ```bash
   python3 -m v4.controller.shell X570 --repl
   ```
5. Verify the full RPC surface after a deploy:
   ```bash
   PYTHONPATH=. python3 -m v4.scripts.verify_v4_remote X570
   ```

## Action Surface (v4)

| Group | Actions |
|---|---|
| Host | `host.capabilities`, `host.probe`, `host.guard`, `host.repair`, `host.policy` |
| Tasks (read) | `host.tasks.list`, `host.tasks.detail` |
| Tasks (write) | `host.task.create`, `host.task.update`, `host.task.run`, `host.task.delete`, `host.tasks.apply` — WRE runs as SYSTEM, so these operate directly under that token (no per-call elevation). `trigger` supports manual/time/logon/boot/interval only; for weekly/other triggers, drive TaskScheduler COM from an in-host script (`file.writeText` + `host.task.create` manual + `host.task.run`). |
| Files | `file.writeText`, `file.readText`, `file.mkdir`, `file.deleteTree`, `file.copy`, `file.putBinary` (base64, ≤ 4 MB), `file.list`, `file.search` |

Removed vs v3: `process.run`/`capture`/`spawn`, `script.run`/`capture`,
`python.run`, `wsl.*`, `everything.search`, `argv-only` policy mode.

## Routing Notes

- All WRE remote-control payloads travel as JSON on SSH stdin. Payloads never
  appear in argv, PowerShell, or `cmd /c` text.
- To launch an arbitrary script/process on the host, register a task on the fly
  (no elevated deploy-wre needed): `host.task.create` with `trigger`=`manual`,
  `run_as_user`=`SYSTEM`, `exe`=`powershell.exe`, `args`=`-File <script>`, then
  `host.task.run` it. The script runs as SYSTEM with full COM access. The
  elevated `deploy-wre.py --tasks-only` step is only the one-time bootstrap for
  the sshd-repair / WRE-Apply agent tasks.
- File transfers larger than 4 MB go through SFTP directly from the controller
  (`v4/controller.sftp`); the `file.putBinary` RPC action is for small in-band
  payloads only.
- The v3 native C# binary and `win-remote` CLI remain in this repo only while
  X570 is still on v3. For X570 / nuc-8-sumie, use only `v4/` (or `v5/` once deployed).

## Deployment

- **Light deploy (no elevation)** — `python3 -m v4.scripts.deploy_sftp <target>`
  SFTP-uploads the embeddable Python + v4 source tree to `C:/CodexRemote/wre/`
  and rewrites `access-policy.json` from the existing v3 token hash.
- **Elevated deploy (one-time, on the Windows host)** — operator elevates and
  runs `C:\CodexRemote\wre\python\pythonw.exe C:\CodexRemote\wre\deploy-wre.py
  --target-name <name> --expected-listen <tailscale-ip> --access-token <plain>`
  to re-create the sshd self-repair scheduled tasks (via TaskScheduler COM,
  not schtasks.exe) and pre-register any app-specific tasks like StreamServ.

## Verification

Use the checks that match the change:

1. `python3 -m v4.controller.shell <target> --probe`
2. `PYTHONPATH=. python3 -m v4.scripts.verify_v4_remote <target>` (full 13-step matrix)
3. `host.tasks.list` if scheduled tasks changed
4. `host.guard` (noDisable=true) if exposure/policy changed
5. `host.task.run` smoke if task lifecycle changed (only after elevation)

## StreamServ Migration Sketch (X570, future)

When X570 rolls to v4:

1. Register scheduled task once at elevated deploy-wre:
   ```
   host.task.create {
     "name": "CodexRemote StreamServ Start",
     "exe": "D:/StreamServ/start_streamserv.bat",
     "trigger": "manual",
     "run_as_user": "SYSTEM",
     "run_level": 1
   }
   ```
2. Trigger remotely:
   ```
   host.task.run { "name": "CodexRemote StreamServ Start" }
   ```
3. Read logs:
   ```
   file.readText { "path": "D:/StreamServ/logs/latest.log" }
   ```

No `process.run`; the controller sends no shell strings over SSH. In-host
PowerShell/COM is runnable via a created task (`file.writeText` + `host.task.create` manual + `host.task.run`).

## Files To Graduate Later

- `windows-remote-executor/` and `windows-remote-executor-native/` will be
  removed from this repo once X570 publishes a v4 release and operator runs
  the elevated deploy.
- `release-assets/*.zip` for v0.3.x will move to `release-assets/legacy-v3/`.