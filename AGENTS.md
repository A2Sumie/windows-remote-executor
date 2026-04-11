# Agent Guide

If you are an agentic tool operating from this repository, use this file as the default operating brief.

## Goal

Use this repository to operate a Windows host from macOS or Linux through the provided executor.

- Local wrapper: `windows-remote-executor/bin/win-remote`
- Structured MCP server: `windows-remote-executor/mcp/win_remote_mcp.py`
- Windows native executor: `windows-remote-executor-native`
- Default stance: SSH on a private address, access policy enabled, PowerShell minimized

## First Steps

1. Read `windows-remote-executor/README.md`.
2. Locate the real target env file outside git-tracked defaults.
3. Start with `./windows-remote-executor/bin/win-remote probe <target>`.
4. For routine agent use, prefer the MCP server over shell-authored command strings.
4. If the task touches host exposure or connectivity, run `./windows-remote-executor/bin/win-remote guard <target>`.
5. Only then perform file transfer, process execution, deploys, or tool updates.

## Command Choice

- Use `win-remote run` for native executables such as `whoami.exe`, `cmdkey.exe`, `tasklist.exe`, `dotnet`, `git`, and app binaries.
- Use `win-remote run` for Windows-native platform tools such as `dism.exe`, `shutdown.exe`, `curl.exe`, and `reg.exe`.
- Use `win-remote wsl`, `win-remote wsl-capture`, `win-remote wsl-sh`, or MCP `win_wsl*` for Linux-side work inside WSL.
- `win-remote wsl-sh` now stages local scripts through file transfer and executes them from WSL ext4 temp space, so it avoids Windows command-line length failures.
- Use `win-remote capture` when output encoding is unknown, localized, UTF-16-shaped, or byte-sensitive and you need stable JSON plus raw base64 bytes.
- Use `win-remote py` for Python scripts on the Windows host.
- Use `win-remote put` and `win-remote get` for file transfer.
- Use `win-remote deploy` for staged directory updates.
- Use `win-remote update-tools` to publish a new Windows-side executor release without overwriting an in-use `.exe`.
- Use `win-remote policy` to install or rotate `access-policy.json`.
- Use `win-remote guard` to validate that `sshd` is still bound safely.
- Use `win-remote repair` when `sshd` validation fails, the service will not stay up, or you need to force the managed config back into place.
- Use `win-remote tasks` or MCP `win_tasks` when you need scheduled-task state; do not hand-author `Get-ScheduledTaskInfo -TaskName ...` for names with spaces.
- Use `win-remote exec --file <script.ps1>` or `--stdin` only when PowerShell is specifically required.
- On `X570`, treat `win-remote cmd` as forbidden unless the operator explicitly asks for a legacy `cmd.exe` builtin.
- Do not send raw PowerShell command lines over SSH. If PowerShell must run, it must go through the wrapper's UTF-8/base64 transport.
- `win-remote run` and `win-remote capture` now block raw `powershell.exe` / `pwsh` by default.
- Silent admin commands such as `put`, `get`, and no-post `deploy` now return `OK` on success so clients do not treat silence as uncertainty.
- `win-remote update-tools` now stages versioned releases under `C:\CodexRemote\tools\releases\...` and flips the stable launcher `C:\CodexRemote\tools\WindowsRemoteExecutor.cmd`.

## PowerShell Rule

Treat raw PowerShell command lines as disallowed.

- Prefer `win-remote exec --file script.ps1`.
- If generating PowerShell dynamically, prefer `--stdin`.
- Do not use inline PowerShell as a normal control path.
- Never bypass the wrapper and send raw `powershell.exe ...`, `pwsh ...`, or hand-rolled `-EncodedCommand`.
- Do not tunnel raw PowerShell through `win-remote run` or `win-remote capture` unless you intentionally pass the legacy override.
- If the goal is machine-readable Windows state, prefer `exec --stdin` plus `ConvertTo-Json -Compress`.
- If the goal is WSL or Linux setup, prefer `win-remote wsl-sh --file`, `--stdin`, or MCP `win_wsl_script` instead of hand-writing `wsl.exe ... bash -lc ...` or `/mnt/c/...` paths.
- Use `win-remote run ... wsl.exe ...` only for Windows-side WSL administration such as install, version selection, or shutdown.
- Keep long-lived models, caches, and active code on WSL ext4 such as `/home/...`, not on `/mnt/*`.
- When a WSL workload depends on a specific interpreter or GPU tool, prefer absolute paths such as `/home/.../.venv/bin/python` and `/usr/lib/wsl/lib/nvidia-smi`.

## Encoding Rule

- Assume localized Windows CLI output may be UTF-16 or codepage-shaped and unsuitable for parsing over SSH.
- Treat `win-remote run` as a human-oriented, best-effort text path.
- Use `win-remote capture` when you need exact bytes, detected encoding labels, or stable machine parsing of stdout/stderr.
- Prefer `wsl-capture` over PTY scraping for machine decisions from Linux-side commands.
- Do not make automation decisions from mojibake text returned by `wsl.exe`, `dism.exe`, `systeminfo.exe`, or any localized CLI unless you captured bytes or emitted JSON on the Windows side.
- Prefer JSON from Windows-local PowerShell when the result is state, and prefer `capture` when the result is process output.

## Security Rule

- Assume the target should remain `private-only` unless the operator explicitly says otherwise.
- Do not weaken or delete `access-policy.json`.
- Do not remove `sshd` guard tasks.
- Do not allow wildcard listeners such as `0.0.0.0` or `::`.
- If a token is required, let `win-remote` forward it from the target env file.

## Verification Rule

After making changes, verify with at least:

1. `win-remote probe <target>`
2. one `win-remote run <target> ...` smoke test
3. `win-remote guard <target>` if networking or policy changed
4. one PowerShell path through `exec --file` or `exec --stdin` only if your change touched PowerShell behavior

## Git Rule

- Do not commit real `targets/*.env` files other than `example.env`.
- Do not commit real access tokens, hostnames, Tailscale IPs, usernames, SSH public keys, logs, or publish output.
- Prefer framework-dependent `.NET 8` publish when the Windows host already has `.NET 8` runtime installed.
- Use self-contained publish only when the host cannot satisfy the runtime requirement.
- For releases, push a version tag and let GitHub Actions build the assets.

## Minimal Workflow

```bash
./windows-remote-executor/bin/win-remote probe <target>
./windows-remote-executor/bin/win-remote run <target> whoami.exe
./windows-remote-executor/bin/win-remote put <target> ./local.file C:/CodexRemote/inbox/local.file
./windows-remote-executor/bin/win-remote deploy <target> ./dist C:/CodexRemote/apps/myapp
./windows-remote-executor/bin/win-remote update-tools <target>
```

## More Templates

- Copy-paste prompt template: `templates/AGENT_INSTRUCTIONS_TEMPLATE.md`
- Claude-oriented entrypoint: `CLAUDE.md`
- Codex-oriented entrypoint: `CODEX.md`
