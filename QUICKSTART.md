# Quick Start

Read `AGENTS.md` first. Use MCP tools for routine Windows work and `windows-remote-executor/bin/win-remote` for manual debugging, deployment, and bootstrap packaging. `win-remote` is a Python shim around the V3 `rpc-stdio` client; there is no older transport fallback.

This repo is the source tree. Parent-workspace executor paths may be symlinks into it. For remote deployment, tag and publish a GitHub release, then deploy release assets.
From the parent livestr workspace, use `git -C windows-remote-executor-public ...` so nested repos are not confused with the workspace root.

## First Commands

```bash
./windows-remote-executor/bin/win-remote probe <target>
./windows-remote-executor/bin/win-remote run <target> whoami.exe
```

## Route Rules

- MCP tools call the Python V3 client directly; prefer them for routine agent control.
- `run`, `capture`, and `spawn` are native argv actions for concrete executables over `process.run`, `process.capture`, and `process.spawn`.
- `capture` is the machine-readable process-output route.
- `exec` and `exec-capture` send a script body through `script.run` / `script.capture`; use `--file` or `--stdin` for script-shaped Windows maintenance.
- The wrapper rejects drive-relative Windows paths such as `D:folderfile.py`; quote backslash paths or use `D:/folder/file.py`.
- The wrapper has a default guard for `powershell.exe` and `pwsh` through `run`, `capture`, and `spawn`; use `--allow-powershell` or `exec` when that is the lower-error choice.
- On `policy --command-mode argv-only`, native policy rejects shell/interpreter executables through `run`, `capture`, and `spawn`.
- `argv-only` still allows V3 script actions for staged maintenance; describe the route choice, not a blanket ban.
- `wsl` and `wsl-capture` call V3 `wsl.run` / `wsl.capture`.
- `wsl-sh` / `wsl-sh-capture` send shell script bodies through V3 `wsl.script` / `wsl.script.capture`.
- `wsl-py` / `wsl-py-capture` carry explicit WSL Python interpreter, cwd, module/script, and args through WSL argv actions.
- `wsl-resident` is for durable WSL services and readiness diagnostics.
- Use `tasks` or MCP `win_tasks` for scheduled-task inspection.
- Use `policy`, `guard`, and `repair` for access policy and `sshd` safety.
- If a workflow needs new remote-control coverage, add a V3 RPC action plus MCP/tool support before adding another quoting convention.

## Boundaries

- Keep targets `private-only` unless the operator changes that requirement.
- Keep token enforcement, `access-policy.json`, and `sshd` guardrails.
- Keep WSL models, caches, venvs, and hot code on ext4 paths such as `/home/...`, not `/mnt/*`.
- Prefer framework-dependent `.NET 8` builds when the host already has `.NET 8`; use self-contained publish for brand-new hosts or drop-and-run packaging.
