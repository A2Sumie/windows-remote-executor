# Windows Remote Executor — v3 LEGACY (read-only)

**This directory is the v3 (PowerShell-b64 / argv / C# native) WRE wrapper. It is
DEPRECATED and lives here only while X570's in-flight ASR tail still uses some
v3-registered scheduled tasks. Do not add new v3 routes.**

## Use v4 instead

The active control plane is `../v4/` — pure-Python, no PowerShell, no argv
process spawn. See:

- `../AGENTS.md` — v4-first agent guide
- `../v4/V4.md` — protocol contract
- `../v4/scripts/verify_v4_remote.py` — verification matrix
- Skill `wre-v4` in `~/.claude/skills/wre-v4/SKILL.md`

## What v4 removed (do not reach back into v3 for these)

| Removed v3 route | v4 replacement |
|---|---|
| `win-remote run/capture/spawn` | register a scheduled task via `host.task.create` (elevated); trigger with `host.task.run` |
| `win-remote exec/exec-capture` (PowerShell/cmd script body) | gone — no PowerShell surface |
| `win-remote py` | register a scheduled task that runs the Python script; trigger remotely |
| `win-remote wsl/wsl-py/wsl-sh/wsl-resident` | gone — WSL control is a separate subsystem |
| `win-remote find` (Everything SDK) | gone — use `file.readText` on a known path, or `pathlib.rglob` via a registered Python task |
| `win-remote put/get` | v4 `file.writeText` / `file.readText` + `v4.controller.sftp` for large binaries |
| `win-remote policy --rotate-token` | v4 `host.policy` with a new plain token |

## Existing v3 release assets

Moved to `../release-assets/legacy-v3/`. The v4 bootstrap zip is the only
release artefact from v0.4.0 onward.

## When this directory is finally removable

When ALL of the following are true:

1. X570 operator runs the elevated `deploy-wre.py --tasks-only` pass to retire
   the v3 self-heal tasks (`CodexRemote Sshd Repair *`) and the v3-registered
   StreamServ tasks.
2. StreamServ is verified to start via `host.task.run` from v4 alone.
3. Any durable WSL workload has been re-anchored under a separate `wsl-bridge`
   skill (or operator has accepted a confirmed-wsl-via-direct-ssh loop).

Then `git mv windows-remote-executor windows-remote-executor-legacy-v3` and
remove the symlink from the livestr workspace root.