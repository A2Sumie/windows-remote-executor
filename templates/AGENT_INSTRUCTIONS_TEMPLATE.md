# Agent Instructions Template

Copy and adapt this block for Codex or another coding/ops agent that should use this repository to operate a Windows host.

```md
Use the Windows Remote Executor in this repository for Windows and WSL control from macOS/Linux.

Repository paths:
- Local wrapper: `windows-remote-executor/bin/win-remote`
- MCP server: `windows-remote-executor/mcp/win_remote_mcp.py`
- Native executor source: `windows-remote-executor-native/src/WindowsRemoteExecutor.Native`

Route rules:
1. Prefer MCP tools for routine agent work. Use `win-remote` for manual debugging, release deployment, and compatibility; updated targets use the `invoke-b64` envelope under the wrapper.
2. Use `run`, `capture`, and `spawn` for concrete Windows executables with explicit argv.
3. Use `capture` when process output needs stable parsing or raw bytes.
4. Use `exec` / `exec-capture` with `--file` or `--stdin` for PowerShell/cmd script maintenance; these routes stage payloads through `exec-file-b64`.
5. The wrapper has a default guard for `powershell.exe` and `pwsh` through `run`, `capture`, and `spawn`; use `--allow-powershell` or another route when that is the lower-error choice.
6. On `policy --command-mode argv-only`, native policy rejects shell/interpreter executables through `run`, `capture`, and `spawn`.
7. `argv-only` still allows staged `exec-file-b64` / `exec-file-capture-b64`; describe the route choice, not a blanket ban.
8. Use `wsl` / `wsl-capture` for direct WSL argv.
9. Use `wsl-sh` / `wsl-sh-capture` for longer Linux shell scripts; the wrapper stages the script through Windows transfer and bootstraps it into WSL `/tmp`.
10. Use `wsl-py` / `wsl-py-capture` for WSL Python with explicit interpreter, cwd, module/script, and args.
11. Use `wsl-resident` for durable WSL services and readiness diagnostics.
12. Use `tasks` or MCP `win_tasks` for scheduled-task inspection.
13. Use `policy`, `guard`, and `repair` for access policy and `sshd` safety.
14. Keep WSL models, caches, venvs, and hot code on ext4 paths such as `/home/...`, not `/mnt/*`.
15. Keep targets `private-only` unless the operator changes that requirement.
16. Keep token enforcement, `access-policy.json`, and `sshd` guardrails in place.

Suggested workflow:
1. Read `AGENTS.md`.
2. Inspect the target env file outside git-tracked defaults.
3. Start with `win-remote probe <target>`.
4. Use MCP tools for routine execution.
5. If policy or exposure is involved, validate with `win-remote guard <target>` and repair with `win-remote repair <target>` when needed.
6. Verify with the smallest route-specific smoke test: native `run`, staged `exec`, WSL capture, or `wsl-resident` proof.

Release workflow:
1. Commit changes on `main`.
2. Push to GitHub.
3. Create and push a version tag.
4. Let GitHub Actions build release assets.
5. Deploy release assets with `win-remote update-tools --native-zip <release-asset.zip>`; do not deploy production updates from local publish directories.
```

## Short Variant

Use this shorter version when the agent context window is tight.

```md
Use this repo's Windows Remote Executor. Prefer MCP tools for routine control and `win-remote` for manual debugging/release deployment. Updated targets use a single `invoke-b64` envelope under the wrapper; older targets fall back to `win-remote-legacy` until updated from a release asset. Choose the route with the lowest expected error rate: native argv for concrete executables, `capture` for parseable process output, staged `exec` for PowerShell/cmd scripts, `wsl`/`wsl-capture` for WSL argv, `wsl-sh` for longer WSL shell scripts, and `wsl-resident` for durable WSL services. The wrapper guards raw PowerShell in argv routes by default but has explicit escape hatches. `argv-only` rejects shell/interpreter executables through native argv routes while still allowing staged `exec-file-b64`. Keep targets private, keep policy and guardrails, keep WSL workloads on ext4, and verify with the route-specific smoke test.
```
