# Codex Quick Start

Read `AGENTS.md` first.

Use `windows-remote-executor/bin/win-remote` as the primary interface for Windows work from this repository.

## First Commands

```bash
./windows-remote-executor/bin/win-remote probe <target>
./windows-remote-executor/bin/win-remote run <target> whoami.exe
```

## Rules

- Prefer `run`, `capture`, `py`, `put`, `get`, `deploy`, `policy`, `guard`, and `update-tools`.
- Use `run` for `wsl.exe`, `dism.exe`, and other Windows-native platform tools instead of wrapping them in PowerShell.
- Use `capture` when output may be UTF-16, locale-codepage, or binary-shaped and you need stable JSON plus raw bytes.
- Treat PowerShell as fallback only.
- If PowerShell is required, prefer `win-remote exec --file <script.ps1>` or `--stdin`.
- If a result needs to be machine-readable, prefer `capture` for process output and `exec --stdin` plus JSON for Windows state.
- For complex WSL setup, upload a `.sh` file and execute it with `wsl.exe ... bash /mnt/c/...`.
- Keep hosts `private-only` unless the operator explicitly says otherwise.
- Do not weaken token enforcement or `sshd` guardrails.
- Do not commit real env files, tokens, host addresses, usernames, SSH keys, logs, or publish outputs.
- Prefer the framework-dependent `.NET 8` build when the Windows host already has `.NET 8` runtime.
