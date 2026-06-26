# Codex Quick Start

Read `AGENTS.md` first. Use MCP tools for routine Windows work and `windows-remote-executor/bin/win-remote` for manual debugging, deployment, and compatibility. `win-remote` is now a Python shim that prefers the native `invoke-b64` JSON envelope and falls back to `win-remote-legacy` only for older targets.

This repo is the source tree. Parent-workspace executor paths may be symlinks into it. For remote deployment, tag and publish a GitHub release, then deploy release assets.
From the parent livestr workspace, use `git -C windows-remote-executor-public ...` so nested repos are not confused with the workspace root.

## First Commands

```bash
./windows-remote-executor/bin/win-remote probe <target>
./windows-remote-executor/bin/win-remote run <target> whoami.exe
```

## Route Rules

- MCP tools call the wrapper; prefer them for routine agent control.
- `run`, `capture`, and `spawn` are native argv routes for concrete executables; updated targets receive those requests as one `invoke-b64` envelope.
- `capture` is the machine-readable process-output route.
- `exec` and `exec-capture` stage PowerShell/cmd payloads through `exec-file-b64`; use `--file` or `--stdin` for script-shaped Windows maintenance.
- The wrapper checks target native support before using `invoke-b64`; older targets use `win-remote-legacy` until updated from a GitHub release asset.
- The wrapper rejects drive-relative Windows paths such as `D:folderfile.py`; quote backslash paths or use `D:/folder/file.py`.
- The wrapper has a default guard for `powershell.exe` and `pwsh` through `run`, `capture`, and `spawn`; use `--allow-powershell` or another route when that is the lower-error choice.
- On `policy --command-mode argv-only`, native policy rejects shell/interpreter executables through `run`, `capture`, and `spawn`.
- `argv-only` still allows staged `exec-file-b64` / `exec-file-capture-b64`; describe the route choice, not a blanket ban.
- `wsl` and `wsl-capture` call `wsl.exe --exec`.
- `wsl-sh` stages a script through Windows transfer, then bootstraps it into WSL `/tmp`.
- `wsl-py` / `wsl-py-capture` carry explicit WSL Python interpreter, cwd, module/script, and args.
- `wsl-resident` is for durable WSL services and readiness diagnostics.
- Use `tasks` or MCP `win_tasks` for scheduled-task inspection.
- Use `policy`, `guard`, and `repair` for access policy and `sshd` safety.
- If a workflow needs new coverage, add an `invoke-b64` action plus MCP/tool support before adding another quoting convention.

## Boundaries

- Keep targets `private-only` unless the operator changes that requirement.
- Keep token enforcement, `access-policy.json`, and `sshd` guardrails.
- Keep WSL models, caches, venvs, and hot code on ext4 paths such as `/home/...`, not `/mnt/*`.
- Prefer framework-dependent `.NET 8` builds when the host already has `.NET 8`; use self-contained publish only when needed.
