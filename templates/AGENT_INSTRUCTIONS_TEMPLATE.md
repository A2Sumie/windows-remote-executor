# Agent Instructions Template

Copy and adapt this block for Codex, Claude Code, or another coding/ops agent that should use this repository to operate a Windows host safely.

```md
You may use the Windows Remote Executor in this repository to operate a Windows host from macOS/Linux.

Repository paths:
- Local wrapper: `windows-remote-executor/bin/win-remote`
- Windows native executor source: `windows-remote-executor-native/src/WindowsRemoteExecutor.Native`

Operating rules:
1. Prefer `win-remote run`, `win-remote py`, `win-remote put`, `win-remote get`, `win-remote deploy`, `win-remote probe`, `win-remote policy`, and `win-remote guard`.
2. Treat PowerShell as a fallback. If PowerShell is required, prefer `win-remote exec --file <script.ps1>` or `--stdin` instead of inline quoting.
3. Do not invoke the Windows native executor directly unless you have a specific reason. Let `win-remote` handle base64 transport and `--access-token`.
4. Assume the host should remain `private-only` unless the operator explicitly says otherwise.
5. Never weaken `access-policy.json`, remove the `sshd` guard, or permit wildcard listeners without explicit operator approval.
6. Never commit real host env files, access tokens, hostnames, Tailscale IPs, usernames, SSH keys, logs, or publish outputs.
7. Prefer the framework-dependent `.NET 8` publish when the Windows host already has the `.NET 8` runtime installed.
8. Use the self-contained publish only when the host cannot satisfy the framework-dependent runtime requirement.

Suggested workflow:
1. Read `windows-remote-executor/README.md` and `windows-remote-executor-native/README.md`.
2. Inspect the target env file outside git-tracked defaults.
3. Start with `win-remote probe <target>`.
4. If needed, validate policy with `win-remote guard <target>`.
5. For tool updates, prefer `win-remote update-tools <target>`.
6. After any change, verify with `probe`, one native `run`, and one guarded PowerShell path only if needed.

Suggested release workflow:
1. Commit changes on `main`.
2. Push to GitHub.
3. Create and push a version tag like `v0.1.2`.
4. Let GitHub Actions build the release assets automatically.
```

## Short Variant

Use this shorter version when the agent context window is tight.

```md
Use this repo's Windows Remote Executor. Prefer `win-remote` over direct PowerShell. Keep hosts `private-only` by default. Do not remove access-token enforcement or `sshd` guardrails. Never commit real env files, tokens, host addresses, usernames, SSH keys, logs, or publish outputs. Prefer the framework-dependent `.NET 8` build when the Windows host already has `.NET 8`; otherwise use the self-contained publish. Validate changes with `probe`, `guard`, and a minimal execution smoke test.
```
