# Agent Guide

Use this repository to operate Windows hosts (X570, nuc-8-sumie / NUC8) from
macOS/Linux through the WRE **v6** control plane.

## Source Of Truth

- v6 agent-first tree (protocol v6, process.*/wsl.*/loop mode/MCP): `v6/` —
  protocol doc `v6/V6.md`. **Daily route since the 2026-08-25 cutover:** both
  X570 and nuc-8-sumie serve v6.1 from `C:/WRE/wre`, the `WRE *` SYSTEM tasks
  point at that tree, and zero enabled tasks reference any legacy tree.
- v5/v4 trees: audit/fix history only, frozen; on-host `C:/CodexRemote/wre`
  (v4) is cold standby and hardcoded-protected forever.
- **v3 graduated 2026-09-16** (see "Graduation Record" below) — the
  `windows-remote-executor/` and `windows-remote-executor-native/` trees were
  removed from this repo; retrieve them from git history only.
- From the parent livestr workspace, run Git commands with
  `git -C windows-remote-executor-public ...`.

## v6 Entry Point

Fixed remote SSH command:

```
C:/WRE/wre/python/python.exe -I -X utf8 C:/WRE/wre/rpc.py rpc-stdio
```

stdin = one UTF-8 JSON request line. stdout = one UTF-8 JSON response line.
No PowerShell/cmd strings travel from the controller (transport constraint).

## First Steps

1. Read `v6/V6.md` (protocol + 32-action surface + rebrand note).
2. Locate the real target env file at `v6/targets/<name>.env` (canonical since
   2026-09-16; resolution chain: `$WRE_TARGETS_DIR` → `v6/targets/` → legacy
   `windows-remote-executor/targets/` code-level fallback, retained one
   release cycle after the v3 tree removal).
3. Probe:
   ```bash
   PYTHONPATH=. python3 -m v6.controller.shell X570 --probe
   ```
4. Interactive / scriptable CLI:
   ```bash
   PYTHONPATH=. python3 -m v6.controller.shell X570 '<action>' '<json>'
   ```
5. Verify the full RPC surface after a deploy:
   ```bash
   PYTHONPATH=. python3 -m v6.scripts.verify_v6_remote X570
   ```

## Action Surface (v6, summary — full schema in V6.md / native/schemas.py)

| Group | Highlights |
|---|---|
| Host | `host.capabilities/probe/guard/repair/policy` |
| Tasks | `host.tasks.*` (list/detail) + `host.task.*` (create/update/run/delete) — WRE runs as SYSTEM; manual/time/logon/boot/interval triggers natively, weekly/other via in-host COM script |
| Files | `file.writeText/readText/mkdir/deleteTree/copy/putBinary(≤4MB)/list/search` |
| Process | `process.start/status/output/stop/list` (job model, supervisor-reaped) |
| WSL | `wsl.list/status/run` |

Removed back in v4 vs v3: `process.run/capture/spawn`, `script.run/capture`,
`python.run`, `everything.search`, `argv-only` policy mode (v6 re-added
`process.*`/`wsl.*` as a job model, not the old fire-and-forget).

## Routing Notes

- All WRE remote-control payloads travel as JSON on SSH stdin. Payloads never
  appear in argv, PowerShell, or `cmd /c` text.
- To run PowerShell or TaskScheduler COM on the host: `file.writeText` the
  script, `host.task.create` (trigger `manual`, `run_as_user`=`SYSTEM`),
  `host.task.run`. Runs as SYSTEM with full COM access.
- File transfers > 4 MB go through SFTP (`v6/controller/sftp.py`);
  `file.putBinary` is for small in-band payloads only.
- MCP lane: `v6/mcp/wre_mcp.py` exposes the 22-tool surface consumed by
  opencode (`wre` server). Failure semantics: `{"error":"transport"}` means
  the ssh session broke — retry once before concluding anything (read-phase
  breaks are NOT auto-resent; write-phase breaks reconnect+resend once).

## Deployment

- **Light deploy (no elevation)** — `PYTHONPATH=. python3 -m v6.scripts.deploy_sftp
  <target>` SFTP-uploads the embeddable Python + v6 tree to `C:/WRE/wre/` and
  rewrites `access-policy.json`. Token resolution order (fail-closed, never a
  null-token policy): `--access-token` → `TARGET_ACCESS_TOKEN` in the env file
  → live policy at `<entry-root>/access-policy.json`.
- **Elevated deploy (one-time, on the Windows host)** — operator elevates and
  runs the in-tree `deploy-wre.py` to (re)create the sshd self-repair
  scheduled tasks and pre-register app tasks (e.g. StreamServ:
  `WRE StreamServ Start`).

## Verification

Use the checks that match the change:

1. `PYTHONPATH=. python3 -m v6.controller.shell <target> --probe`
2. `PYTHONPATH=. python3 -m v6.scripts.verify_v6_remote <target>` (full matrix)
3. `host.tasks.list` if scheduled tasks changed
4. `host.guard` if exposure/policy changed
5. Offshore gates before touching the fleet:
   `<livestr>/.agent/venv/bin/python -m pytest v6/tests/ -q`
   (360+ unit/integration + `v6/tests/chaos/` chaos matrix, seeded+time-boxed)

## Graduation Record (2026-09-16)

- Removed trees: `windows-remote-executor/` (v3 Python wrapper/CLI/MCP),
  `windows-remote-executor-native/` (v3 C# executor). Graduation condition
  ("kept only until X570 rolls to v4") was long past — fleet is on v6.1.
  Their 23 legacy tests retired with the tree; v6 coverage lives in
  `v6/tests/`. Retrieve from git history if ever needed.
- `v6/scripts/deploy_sftp.py`: dropped the v3-lib import and the "read token
  via the v3 bridge" fallback (v3 bridge is dead on the fleet; both daily
  targets carry `TARGET_ACCESS_TOKEN`). Token resolution is now
  a/b/c per above.
- Target `.env` files live in `v6/targets/` (migrated 2026-09-16, modes
  preserved, double .gitignore protection).
- `release-assets/legacy-v3/` already holds the v0.3.x SHA256SUMS.
- Still physically on the host by design (do NOT "rebrand"): v4 cold-standby
  tree `C:/CodexRemote/wre` (protected prefix), `C:/CodexRemote/tools/` v3
  artifacts. See V6.md "Rebrand note".
