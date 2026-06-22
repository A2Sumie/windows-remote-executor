# Agent Guide

Use this repository to operate Windows hosts from macOS or Linux through structured executor routes.

## Source Of Truth

- Wrapper: `windows-remote-executor/bin/win-remote`
- MCP server: `windows-remote-executor/mcp/win_remote_mcp.py`
- Native executor: `windows-remote-executor-native/src/WindowsRemoteExecutor.Native`
- Parent-workspace `windows-remote-executor/` and `windows-remote-executor-native/` paths may be symlinks into this repo. Keep this repo as the source tree.
- From the parent livestr workspace, run Git commands with `git -C windows-remote-executor-public ...` or an explicit owning repo path.
- Deploy executor updates from GitHub release assets after a tag/release. Local publish output is for development smoke tests.

## First Steps

1. Read `windows-remote-executor/README.md`.
2. Locate the real target env file outside git-tracked defaults.
3. Start with `./windows-remote-executor/bin/win-remote probe <target>`.
4. For routine agent use, prefer MCP tools over shell-authored command strings.
5. If the task touches exposure, policy, or connectivity, run `./windows-remote-executor/bin/win-remote guard <target>`.

## Route Choice

- MCP tools call the wrapper; use them for routine agent work: `win_probe`, `win_run`, `win_capture`, `win_wsl*`, `win_exec*`, `win_tasks`, `win_put`, `win_get`, `win_guard`, and `win_repair`.
- `run`, `capture`, and `spawn` are native argv routes. Use them for concrete Windows executables such as `whoami.exe`, `tasklist.exe`, `reg.exe`, `curl.exe`, `dism.exe`, `dotnet`, `git`, and app binaries.
- `capture` returns structured output and raw bytes; use it when process output may be localized, UTF-16, codepage-shaped, or byte-sensitive.
- `exec` and `exec-capture` stage PowerShell or cmd payloads through the native `exec-file-b64` bridge. Use `--file` or `--stdin` for Windows state or maintenance scripts.
- `exec --shell cmd` and compatibility `cmd` are for cmd-shaped scripts.
- The wrapper checks target native help before using staged exec and staged file copy. If an older target lacks `exec-file-b64`, `exec-file-capture-b64`, or `copy-file-b64`, it runs the same staged file through structured `run-b64` or `capture-b64` and prints a compatibility warning on stderr.
- If an older target lacks `spawn-b64`, the wrapper fails before launch with an update/fallback instruction instead of surfacing native `Unknown command`.
- `py` is for Python scripts on the Windows host.
- `wsl` and `wsl-capture` call `wsl.exe --exec <program> <args>`.
- `wsl-py` and `wsl-py-capture` run WSL Python through explicit interpreter, cwd, module/script, and args.
- `wsl-sh` and `wsl-sh-capture` upload a script to the Windows stage, translate the path into WSL, and bootstrap it into `/tmp/windows-remote-executor-*.sh` before execution.
- `wsl-resident` stages a WSL script and returns readiness diagnostics for durable services.
- `put`, `get`, and `deploy` move files. Silent successful admin routes return `OK`.
- `policy`, `guard`, and `repair` manage access policy and `sshd` safety.
- `tasks` or MCP `win_tasks` inspect scheduled tasks.
- `update-tools --native-zip <release-asset.zip>` deploys a release asset and flips `C:\CodexRemote\tools\WindowsRemoteExecutor.cmd`.

## Route And Policy Boundary

- Prefer the route with the lowest expected error rate for the concrete task. In normal agent work, that is MCP, structured argv, staged payload files, `capture`, or staged `exec`.
- The wrapper has a default guard for `powershell.exe` and `pwsh` through `run`, `capture`, and `spawn`; `--allow-powershell` and `WIN_REMOTE_ALLOW_RAW_POWERSHELL=1` are explicit escape hatches when that route is lower-risk.
- On `policy --command-mode argv-only`, native policy rejects shell/interpreter executables through `run`, `capture`, and `spawn`.
- `argv-only` still allows staged `exec-file-b64` and `exec-file-capture-b64`. Use that bridge when script-shaped maintenance is the lower-error route under this policy.
- Record the actual route choice and verification evidence instead of reducing the policy to a blanket PowerShell claim.
- If existing routes cannot represent a workflow without fragile quoting, add a native subcommand or MCP tool before adding another quoting convention.
- Use forward slashes for Windows paths, for example `D:/StreamServ/auto_stream.py`, or quote backslash paths. The wrapper rejects drive-relative shapes such as `D:StreamServauto_stream.py` because they usually mean the local shell stripped backslashes.

## WSL Boundary

- Use `wsl`/`wsl-capture` for direct Linux argv.
- Use `wsl-sh --file` or `--stdin` for longer Linux shell scripts.
- Use `wsl-resident` when the goal is a long-lived WSL service; treat launch success as provisional until delayed listener, health, process, log, or GPU proof passes.
- Keep long-lived models, caches, venvs, and hot code on WSL ext4 paths such as `/home/...`, not `/mnt/*`.
- Use absolute WSL paths for brittle interpreters and GPU tools, for example `/home/.../.venv/bin/python` and `/usr/lib/wsl/lib/nvidia-smi`.
- Use `win-remote run ... wsl.exe ...` only for Windows-side WSL administration such as install, version selection, or shutdown.

## Encoding And Parsing

- Treat localized Windows CLI text as human-oriented unless captured bytes or JSON prove otherwise.
- Prefer `capture` for process output decisions.
- Prefer `exec --stdin` plus `ConvertTo-Json -Compress` for Windows state decisions.
- Prefer `wsl-capture` over PTY scraping for machine decisions from Linux commands.

## Security

- Keep targets `private-only` unless the operator explicitly changes that requirement.
- Keep `access-policy.json`, token enforcement, and `sshd` guard tasks in place.

## Verification

Use the checks that match the change:

1. `win-remote probe <target>`
2. one native argv smoke test such as `win-remote run <target> whoami.exe`
3. `win-remote guard <target>` if networking or policy changed
4. one `exec --file` or `exec --stdin` path if staged Windows script behavior changed
5. one `wsl-capture`, `wsl-sh-capture`, or `wsl-resident` proof if WSL behavior changed
6. `scripts/verify-remote-cases.sh <target>` before release deployment when the target is available

## Minimal Workflow

```bash
./windows-remote-executor/bin/win-remote probe <target>
./windows-remote-executor/bin/win-remote run <target> whoami.exe
./windows-remote-executor/bin/win-remote put <target> ./local.file C:/CodexRemote/inbox/local.file
./windows-remote-executor/bin/win-remote deploy <target> ./dist C:/CodexRemote/apps/myapp
./windows-remote-executor/bin/win-remote update-tools --native-zip <release-asset.zip>
```

## More Templates

- Copy-paste prompt template: `templates/AGENT_INSTRUCTIONS_TEMPLATE.md`
- Codex-oriented entrypoint: `CODEX.md`
