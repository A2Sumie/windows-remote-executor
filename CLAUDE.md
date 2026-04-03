# Claude Code Quick Start

Read `AGENTS.md` first.

Use this repository's executor instead of inventing your own Windows transport.

## First Commands

```bash
./windows-remote-executor/bin/win-remote probe <target>
./windows-remote-executor/bin/win-remote run <target> whoami.exe
```

## Rules

- Prefer `run`, `capture`, `py`, `put`, `get`, `deploy`, `policy`, `guard`, `repair`, and `update-tools`.
- Use `run` for `wsl.exe`, `dism.exe`, and other Windows-native platform tools instead of wrapping them in PowerShell.
- Use `capture` when output may be UTF-16, locale-codepage, or binary-shaped and you need stable JSON plus raw bytes.
- Treat PowerShell as fallback only.
- If PowerShell is required, it must go through `win-remote exec --file <script.ps1>` or `--stdin`, which uses the wrapper's UTF-8/base64 path.
- Do not send raw `powershell.exe`, `pwsh`, or hand-rolled `-EncodedCommand` over SSH.
- If a result needs to be machine-readable, prefer `capture` for process output and `exec --stdin` plus JSON for Windows state.
- For complex WSL setup, upload a `.sh` file and execute it with `wsl.exe ... bash /mnt/c/...`.
- Keep hosts `private-only` unless the operator explicitly says otherwise.
- Do not weaken token enforcement or `sshd` guardrails.
- Do not commit real env files, tokens, host addresses, usernames, SSH keys, logs, or publish outputs.
- Prefer the framework-dependent `.NET 8` build when the Windows host already has `.NET 8` runtime.
