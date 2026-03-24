# Codex Quick Start

Read `AGENTS.md` first.

Use `windows-remote-executor/bin/win-remote` as the primary interface for Windows work from this repository.

## First Commands

```bash
./windows-remote-executor/bin/win-remote probe <target>
./windows-remote-executor/bin/win-remote run <target> whoami.exe
```

## Rules

- Prefer `run`, `py`, `put`, `get`, `deploy`, `policy`, `guard`, and `update-tools`.
- Treat PowerShell as fallback only.
- If PowerShell is required, prefer `win-remote exec --file <script.ps1>` or `--stdin`.
- Keep hosts `private-only` unless the operator explicitly says otherwise.
- Do not weaken token enforcement or `sshd` guardrails.
- Do not commit real env files, tokens, host addresses, usernames, SSH keys, logs, or publish outputs.
- Prefer the framework-dependent `.NET 8` build when the Windows host already has `.NET 8` runtime.
