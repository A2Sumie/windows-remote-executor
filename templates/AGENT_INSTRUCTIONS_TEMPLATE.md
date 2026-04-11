# Agent Instructions Template

Copy and adapt this block for Codex, Claude Code, or another coding/ops agent that should use this repository to operate a Windows host safely.

```md
You may use the Windows Remote Executor in this repository to operate a Windows host from macOS/Linux.

Repository paths:
- Local wrapper: `windows-remote-executor/bin/win-remote`
- Structured MCP server: `windows-remote-executor/mcp/win_remote_mcp.py`
- Windows native executor source: `windows-remote-executor-native/src/WindowsRemoteExecutor.Native`

Operating rules:
1. Prefer `win-remote run`, `win-remote capture`, `win-remote wsl`, `win-remote wsl-capture`, `win-remote wsl-sh`, `win-remote py`, `win-remote put`, `win-remote get`, `win-remote deploy`, `win-remote probe`, `win-remote policy`, `win-remote guard`, and `win-remote repair`.
2. For routine agent use, prefer the structured MCP server instead of composing shell command lines.
2. Treat PowerShell as a fallback. If PowerShell is required, it must go through `win-remote exec --file <script.ps1>` or `--stdin`, which uses the wrapper's UTF-8/base64 transport.
3. Use `win-remote run` for Windows-native platform tools such as `dism.exe`, `shutdown.exe`, `curl.exe`, and `reg.exe`.
4. On `X570`, treat `win-remote cmd` as forbidden unless the operator explicitly asks for a legacy `cmd.exe` builtin.
5. If the result is process output and needs stable parsing, prefer `win-remote capture`, which returns JSON with detected encodings plus raw base64 stdout/stderr bytes.
6. If the result is Windows state, prefer `win-remote exec --stdin` and emit JSON from Windows-local PowerShell instead of parsing localized CLI output over SSH.
7. For WSL/Linux setup, prefer `win-remote wsl`, `wsl-capture`, or `wsl-sh --file/--stdin` instead of nesting `wsl.exe ... bash -lc ...` or `/mnt/c/...` paths. `wsl-sh` stages scripts through file transfer, so it is the safe path for longer scripts too.
8. Do not invoke the Windows native executor directly unless you have a specific reason. Let `win-remote` handle base64 transport and `--access-token`.
9. Do not send raw `powershell.exe`, `pwsh`, or hand-rolled `-EncodedCommand` over SSH.
10. Do not tunnel raw `powershell.exe` or `pwsh` through `win-remote run` or `win-remote capture`; those paths should fail closed by default.
11. Assume the host should remain `private-only` unless the operator explicitly says otherwise.
12. Never weaken `access-policy.json`, remove the `sshd` guard, or permit wildcard listeners without explicit operator approval.
13. Never commit real host env files, access tokens, hostnames, Tailscale IPs, usernames, SSH keys, logs, or publish outputs.
14. Use `win-remote run ... wsl.exe ...` only for Windows-side WSL administration such as install, version selection, or shutdown.
15. Keep long-lived models, caches, venvs, and hot code on WSL ext4 paths such as `/home/...`, not on `/mnt/*`, and prefer absolute WSL interpreter paths plus `/usr/lib/wsl/lib/nvidia-smi` for brittle tooling.
16. Prefer the framework-dependent `.NET 8` publish when the Windows host already has the `.NET 8` runtime installed.
17. Use the self-contained publish only when the host cannot satisfy the framework-dependent runtime requirement.

Suggested workflow:
1. Read `windows-remote-executor/README.md` and `windows-remote-executor-native/README.md`.
2. Inspect the target env file outside git-tracked defaults.
3. Start with `win-remote probe <target>`.
4. Prefer MCP tools for routine execution; fall back to shell only when needed for manual debugging.
4. If needed, validate policy with `win-remote guard <target>` and restore service state with `win-remote repair <target>`.
5. For tool updates, prefer `win-remote update-tools <target>`.
6. After any change, verify with `probe`, one native `run`, and one PowerShell path through `exec` only if the change explicitly touched PowerShell behavior.

Suggested release workflow:
1. Commit changes on `main`.
2. Push to GitHub.
3. Create and push a version tag like `v0.1.2`.
4. Let GitHub Actions build the release assets automatically.
```

## Short Variant

Use this shorter version when the agent context window is tight.

```md
Use this repo's Windows Remote Executor. Prefer the MCP server for routine agent control and `win-remote` for manual debugging. PowerShell, if unavoidable, must go through the wrapper's UTF-8/base64 path, not `run` or `capture`. Keep hosts `private-only` by default. Do not remove access-token enforcement or `sshd` guardrails. Never commit real env files, tokens, host addresses, usernames, SSH keys, logs, or publish outputs. Prefer the framework-dependent `.NET 8` build when the Windows host already has `.NET 8`; otherwise use the self-contained publish. Validate changes with `probe`, `guard`, `repair`, and a minimal execution smoke test.
```
