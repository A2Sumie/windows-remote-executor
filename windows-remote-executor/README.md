# Windows Remote Executor

This toolkit lets a macOS or Linux shell drive a Windows machine over SSH without making PowerShell the primary transport. It is built for Codex and similar agentic tools that need reliable file transfer, native process launch, JSON probing, and a PowerShell fallback that does not depend on fragile local quoting.

For agentic clients, the preferred entrypoint is the C# native executor through the structured MCP server in `MCP.md`. Do not treat shell command generation as the normal control plane. The shell wrapper exists to resolve targets, upload payloads, and call the native executable; the native executable is responsible for structured argv, base64 payloads, process launch, output capture, WSL bridging, and SSH safety checks.

This is a hard stability boundary: if a task can be expressed as argv, script body, file upload/download, WSL program, scheduled-task query, or PowerShell file/stdin, use the native/MCP path. Avoid raw `cmd.exe`, raw `powershell.exe`, `wsl.exe ... bash -lc ...`, or hand-built Windows command strings in agent work.

The intended steady state is:

- direct native process launch, `scp`, and a native C# Windows executor for routine work
- structured argv/base64 transport instead of quote-sensitive command strings
- PowerShell or cmd script control only through the wrapper's staged exec bridge
- SSH bound to a private address by default, with an on-host guard that disables `sshd` if exposure drifts

## What It Does

- runs remote native processes without a shell hop
- captures remote native process output as JSON with detected encodings and raw base64 stdout/stderr bytes
- runs remote Python scripts, including `conda run`
- stages PowerShell/cmd scripts as files before the native executor reads and launches them
- ships a minimal stdio MCP server so agents can call structured tools instead of composing shell strings
- runs WSL Python scripts/modules through explicit Python executable, cwd, and argv so venv/conda/model environments do not need nested activation strings
- launches WSL resident services with verified PID or port readiness instead of leaving callers to guess between `tmux` and `nohup`
- uploads and downloads files with `scp`
- collects a JSON probe from the target host
- deploys a directory through a remote staging area
- installs an access policy with an optional access token hash
- installs an `sshd` guard that disables the service if it listens on an unexpected address
- installs `sshd` repair watch tasks and service recovery actions
- exposes structured scheduled-task inspection so task names with spaces stay out of ad hoc PowerShell
- hot-updates the remote tool directory with backups

## Directory Layout

```text
windows-remote-executor/
├── bin/win-remote
├── lib/common.sh
├── mcp/
│   └── win_remote_mcp.py
├── targets/example.env
└── windows/
    ├── bootstrap-host.ps1
    ├── bootstrap-x570.ps1
    ├── install-openssh-executor.ps1
    └── probe.ps1
```

`bootstrap-x570.ps1` is now just a legacy compatibility wrapper around the generic `bootstrap-host.ps1`.

## Bootstrap the Windows Host

Run the PowerShell bootstrap script from an elevated PowerShell session on the Windows machine:

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force
cd C:\path\to\windows-remote-executor\windows
.\bootstrap-host.ps1 `
  -PublicKeyPath C:\Users\you\.ssh\id_ed25519.pub `
  -TargetUser Administrator `
  -ListenAddress 100.101.102.103
```

Or use the native bootstrap command directly:

```powershell
.\WindowsRemoteExecutor.Native.exe bootstrap `
  --public-key-file C:\Users\you\.ssh\id_ed25519.pub `
  --user Administrator `
  --listen-address 100.101.102.103
```

Bootstrap prepares OpenSSH, writes `sshd_config`, scopes the firewall to the chosen local IP, installs authorized keys, creates `C:\CodexRemote\{tools,inbox,staging,apps,logs}`, removes any legacy `cmd` recovery artifacts, and installs headless `repair-sshd` scheduled tasks that invoke the stable `WindowsRemoteExecutor.cmd` launcher.

The logon/startup repair path is now fully headless. There is no Startup-folder batch file, no `cmd.exe` recovery window, and no `RunAs` prompt at sign-in. Three scheduled tasks cover the steady state instead:

- `CodexRemote Sshd Repair Logon`
- `CodexRemote Sshd Repair Startup`
- `CodexRemote Sshd Repair Watch`

Each task runs `WindowsRemoteExecutor.cmd repair-sshd`, so recovery no longer depends on `cmd.exe` batch parsing and future hot updates can switch to a new versioned native payload without stopping older executor processes.

## Define a Target

Copy the example target file:

```bash
cp windows-remote-executor/targets/example.env windows-remote-executor/targets/winbox.env
```

Fill in the target:

```bash
TARGET_NAME=winbox
TARGET_HOST=100.101.102.103
TARGET_USER=Administrator
TARGET_PORT=22
TARGET_KEY=/Users/you/.ssh/id_ed25519
TARGET_EXPECTED_LISTEN_ADDRESS=100.101.102.103
TARGET_EXPOSURE_MODE=private-only
TARGET_POLICY_LABEL='PRIVATE-ONLY TOKEN-REQUIRED'
TARGET_ACCESS_TOKEN=replace-with-a-random-token
```

`targets/*.env` is ignored except for `example.env`, so host addresses and tokens stay out of git by default.

## Usage

Probe the remote host:

```bash
./windows-remote-executor/bin/win-remote probe winbox
./windows-remote-executor/bin/win-remote probe winbox --out ./probe-winbox.json
```

For agent clients, use the MCP server so the model calls structured tools instead of authoring shell:

```bash
python3 ./windows-remote-executor/mcp/win_remote_mcp.py
```

Run PowerShell from a local file so the local shell never has to escape it or inline a base64 payload:

```bash
./windows-remote-executor/bin/win-remote exec winbox --file ./scripts/check-host.ps1
cat ./scripts/check-host.ps1 | ./windows-remote-executor/bin/win-remote exec winbox --stdin
./windows-remote-executor/bin/win-remote exec-capture winbox --out ./ps-state.json --stdin < ./scripts/check-host.ps1
```

Run native programs and Python without a shell hop:

```bash
./windows-remote-executor/bin/win-remote run winbox --cwd C:/CodexRemote/inbox whoami.exe
./windows-remote-executor/bin/win-remote py winbox C:/CodexRemote/inbox/echo_args.py --cwd C:/CodexRemote/inbox -- --plain alpha beta
```

Run Linux programs and shell scripts inside WSL without composing `wsl.exe ... bash -lc ...`:

```bash
./windows-remote-executor/bin/win-remote wsl winbox --cwd /tmp /usr/bin/whoami
./windows-remote-executor/bin/win-remote wsl-capture winbox --out ./wsl-uname.json /usr/bin/uname -a
./windows-remote-executor/bin/win-remote wsl-capture winbox --heartbeat-seconds 10 /usr/bin/python3 -c 'import time; time.sleep(30); print("done")'
./windows-remote-executor/bin/win-remote wsl-py-capture winbox --cwd /home/sumie/app --python /home/sumie/app/.venv/bin/python --out ./py-version.json --module platform
./windows-remote-executor/bin/win-remote wsl-py winbox --cwd /home/sumie/app --python /home/sumie/app/.venv/bin/python scripts/task.py -- --input data.json
./windows-remote-executor/bin/win-remote wsl-sh winbox --cwd /tmp --file ./scripts/check-linux.sh -- --flag alpha
cat ./scripts/check-linux.sh | ./windows-remote-executor/bin/win-remote wsl-sh winbox --stdin -- --flag alpha
cat ./scripts/run-server.sh | ./windows-remote-executor/bin/win-remote wsl-resident winbox --cwd /home/sumie/app --log-file /home/sumie/app/logs/server.log --pid-file /home/sumie/app/run/server.pid --port 8023 --stdin
```

`wsl-sh` stages the script through `scp`, copies it into a Linux-native temp path such as `/tmp/...` inside WSL, and executes it there. That avoids Windows command-line length failures and avoids accidentally running the script body straight from `/mnt/c/...`.

For WSL Python work, prefer `wsl-py` / `wsl-py-capture` over shell activation chains. Pass an absolute interpreter such as `/home/.../.venv/bin/python`, a Linux cwd, a module or script path, and script args after `--`. This keeps Python environment selection explicit and avoids multi-layer quoting around `source`, `conda activate`, `python -c`, and backticks.

When a foreground WSL command is expected to stay quiet for a while, pass `--heartbeat-seconds <n>` so the executor emits lightweight stderr heartbeats instead of forcing each workspace to invent its own keepalive prints.

For long-lived WSL services, prefer `wsl-resident` over repo-local `tmux` or `nohup` policy. It stages the script, copies it into WSL temp space, launches it in a detached session, and returns structured JSON with PID, readiness status, listener snapshot, and recent log lines.

Capture localized or byte-sensitive output as JSON:

```bash
./windows-remote-executor/bin/win-remote capture winbox netsh.exe interface ipv4 show interfaces
./windows-remote-executor/bin/win-remote capture winbox --out ./netsh-interfaces.json netsh.exe interface ipv4 show interfaces
```

Deploy a directory and optionally run a post-step through the Windows-local PowerShell decoder:

```bash
./windows-remote-executor/bin/win-remote deploy winbox ./dist C:/CodexRemote/apps/myapp \
  --post-file ./scripts/deploy-post.ps1
```

Inspect the local PowerShell transport:

```bash
./windows-remote-executor/bin/win-remote ps-encode --file ./scripts/check-host.ps1
./windows-remote-executor/bin/win-remote ps-check --file ./scripts/check-host.ps1
./windows-remote-executor/bin/win-remote ps-decode '<utf8-base64>'
```

Install or refresh the remote access policy and guard:

```bash
./windows-remote-executor/bin/win-remote policy winbox
./windows-remote-executor/bin/win-remote guard winbox
./windows-remote-executor/bin/win-remote repair winbox
```

Rotate the local token and re-install the policy:

```bash
./windows-remote-executor/bin/win-remote policy winbox --rotate-token
```

Hot-update the remote tool directory from a release asset:

```bash
gh release download vX.Y.Z -p 'windows-remote-executor-native-vX.Y.Z-fdd-win-x64.zip' -D /tmp/wre-release
./windows-remote-executor/bin/win-remote update-tools winbox --native-zip /tmp/wre-release/windows-remote-executor-native-vX.Y.Z-fdd-win-x64.zip
```

Local publish directories are for development verification. Remote executor updates should use GitHub release assets unless the operator explicitly asks for a local smoke deployment.

Inspect scheduled tasks without hand-writing PowerShell quoting:

```bash
./windows-remote-executor/bin/win-remote tasks winbox
./windows-remote-executor/bin/win-remote tasks winbox --task-name "CodexRemote Sshd Repair Watch"
```

`update-tools` now uploads the current native payload into `C:\CodexRemote\tools\releases\<timestamp>\`, refreshes `C:\CodexRemote\tools\WindowsRemoteExecutor.cmd`, and writes `C:\CodexRemote\tools\current-release.txt`. That lets the control plane flip to a new release even when older `WindowsRemoteExecutor.Native.exe` processes are still running.

## Security Model

The guard logic is intentionally conservative.

- private mode accepts only standard private IPv4 ranges: `10.0.0.0/8`, `100.64.0.0/10`, `192.168.0.0/16`
- loopback and link-local are accepted for local recovery scenarios
- wildcard listeners such as `0.0.0.0` and `::` are treated as unsafe
- if `sshd` drifts away from the expected listen address, `guard-sshd` stops the service and changes startup to demand
- `sshd` is configured with Windows service failure restart actions and a repair watch scheduled task
- `public-with-token` is allowed only when the policy explicitly says so and an access token hash is configured
- the probe and guard output always surfaces the policy label, exposure mode, and whether a token is required

When `access-policy.json` contains an access token hash, native commands such as `probe`, `run-b64`, `capture-b64`, `spawn-b64`, `python-b64`, `powershell-b64`, `exec-file-b64`, `exec-file-capture-b64`, the WSL commands, and `everything-b64` require the matching token. The wrapper automatically forwards `TARGET_ACCESS_TOKEN` as a base64 argument.

## Notes

- Remote paths should use forward slashes, for example `C:/CodexRemote/apps/myapp`.
- For new automation, prefer adding a native subcommand or MCP tool over adding another shell quoting convention. The goal is to make spaces, quotes, non-ASCII text, and long scripts boring.
- `probe`, `run`, `capture`, `py`, `exec`, `guard`, `repair`, and `policy` now prefer `C:/CodexRemote/tools/WindowsRemoteExecutor.cmd` and fall back to `C:/CodexRemote/tools/WindowsRemoteExecutor.Native.exe` when the launcher has not been installed yet.
- `repair` is the explicit self-heal path for `sshd` config, host keys, scoped firewall state, and service startup.
- Use `tasks` when you need scheduled-task state. It avoids the common `Get-ScheduledTaskInfo -TaskName ...` quoting failures around names with spaces.
- Use `wsl`, `wsl-capture`, `wsl-sh`, and `wsl-resident` for Linux-side work inside WSL. They avoid the common `wsl.exe ... bash -lc ...` and `/mnt/c/...` quoting failures.
- Use `wsl-py` and `wsl-py-capture` for WSL Python venv/conda/model environments. Prefer absolute Python paths and argv over activation strings.
- Use `--heartbeat-seconds` on long quiet foreground WSL commands before resorting to dummy app-layer logging.
- `wsl.exe` under `run` is still fine for Windows-side WSL administration such as `--install`, `--set-default-version`, and `--shutdown`, but not for Linux-side workload launch.
- Keep long-lived models, caches, virtualenvs, and hot code on the WSL ext4 filesystem such as `/home/...`, not under `/mnt/c` or `/mnt/d`, or load times will collapse.
- If you update Windows-side files for a WSL workload, explicitly copy them into the WSL ext4 working tree before you trust the result. A changed `D:/...` tree does not automatically mean `/home/...` is updated.
- Inside WSL, prefer absolute executables for brittle dependencies. For example, use `/usr/lib/wsl/lib/nvidia-smi` for GPU queries and absolute venv interpreters such as `/home/sumie/amt_asr_wsl/.venv-vllm/bin/python` for workload entrypoints.
- Do not turn WSL Python work into nested `bash -lc`, command substitution, or backtick-heavy inline Python when `wsl-py` can carry the same operation as argv.
- Prefer `run` for human-facing command execution and progress logs.
- Prefer `capture` or `wsl-capture` when stdout/stderr may be UTF-16, locale-codepage, binary-adjacent, or otherwise too brittle for plain PTY parsing.
- Prefer `spawn` for Windows-side resident/background processes. It sends every argument as UTF-8 base64 to native `spawn-b64`; the native side stages a one-shot scheduled task and then calls `CreateProcessW` from that detached context, with stdout/stderr file handles opened by the bridge. The caller should treat the returned JSON as a launch receipt; `ProcessId` can be `0` when Task Scheduler is used as the launcher.
- On `X570`, prefer direct native executables through `run` for argv-shaped work. For shell-shaped work, use staged `exec --shell powershell` or `exec --shell cmd`; `win-remote cmd` is a compatibility wrapper over the same staged cmd bridge.
- Legacy direct-over-SSH PowerShell fallback was removed. If PowerShell is needed, the native executor must be present.
- Treat raw `powershell.exe`, `pwsh`, and hand-rolled `-EncodedCommand` transport as unsupported. `run` and `capture` now block raw PowerShell by default; use `win-remote exec --file` or `--stdin` so the wrapper owns staging and native execution.
- Silent admin commands such as `put`, `get`, `deploy` without `--post`, `update-tools` without `--install-guard`, and `policy --no-run-guard` now print `OK` on success so agent clients do not misread silence as uncertainty.
- `find` still relies on an externally staged `es.exe`.
- The PowerShell route is now `local script/file/stdin -> scp staging -> WindowsRemoteExecutor.Native.exe exec-file-b64 -> PowerShell -EncodedCommand`, so the SSH command line carries only a short staged path.
