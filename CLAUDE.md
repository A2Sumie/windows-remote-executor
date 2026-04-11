# Claude Code Quick Start

Read `AGENTS.md` first.

Use this repository's executor instead of inventing your own Windows transport.
For routine agent use, prefer the structured MCP server in `windows-remote-executor/mcp/win_remote_mcp.py`.

## First Commands

```bash
./windows-remote-executor/bin/win-remote probe <target>
./windows-remote-executor/bin/win-remote run <target> whoami.exe
```

## Rules

- Prefer `run`, `capture`, `wsl`, `wsl-capture`, `wsl-sh`, `py`, `put`, `get`, `deploy`, `policy`, `guard`, `repair`, `tasks`, and `update-tools`.
- Use `run` for `dism.exe` and other Windows-native platform tools instead of wrapping them in PowerShell.
- Use `wsl`, `wsl-capture`, or `wsl-sh` for Linux-side execution inside WSL.
- `wsl-sh` now stages scripts through file transfer and runs them from a Linux temp path, so `--file` and `--stdin` are safe for longer scripts.
- Use `capture` when output may be UTF-16, locale-codepage, or binary-shaped and you need stable JSON plus raw bytes.
- On `X570`, do not use `win-remote cmd` as part of the normal control path.
- Treat PowerShell as fallback only.
- If PowerShell is required, it must go through `win-remote exec --file <script.ps1>` or `--stdin`, which uses the wrapper's UTF-8/base64 path.
- `run` and `capture` now reject raw `powershell.exe` / `pwsh` by default.
- Do not send raw `powershell.exe`, `pwsh`, or hand-rolled `-EncodedCommand` over SSH.
- If a result needs to be machine-readable, prefer `capture` for process output and `exec --stdin` plus JSON for Windows state.
- Prefer `tasks` or MCP `win_tasks` for scheduled-task inspection instead of handwritten `Get-ScheduledTaskInfo` calls.
- `update-tools` now publishes a versioned release and flips `C:\CodexRemote\tools\WindowsRemoteExecutor.cmd`, so it can succeed while older executor processes are still running.
- For complex WSL setup, use `wsl-sh --file`, `wsl-sh --stdin`, or MCP `win_wsl_script` instead of `wsl.exe ... bash -lc ...`.
- Keep WSL models, caches, venvs, and hot code on ext4 paths such as `/home/...`, and prefer absolute interpreter paths inside WSL.
- Keep hosts `private-only` unless the operator explicitly says otherwise.
- Do not weaken token enforcement or `sshd` guardrails.
- Do not commit real env files, tokens, host addresses, usernames, SSH keys, logs, or publish outputs.
- Prefer the framework-dependent `.NET 8` build when the Windows host already has `.NET 8` runtime.
