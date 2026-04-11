# Windows Remote Executor

This toolkit lets a macOS or Linux shell drive a Windows machine over SSH without making PowerShell the primary transport. It is built for Codex and similar agentic tools that need reliable file transfer, native process launch, JSON probing, and a PowerShell fallback that does not depend on fragile local quoting.

For agentic clients, the preferred entrypoint is now the structured MCP server in `MCP.md`, not ad hoc shell command generation.

The intended steady state is:

- direct native process launch, `scp`, and a native Windows executor for routine work
- PowerShell only when the task is specifically PowerShell-shaped, and only through the wrapper's UTF-8/base64 path
- SSH bound to a private address by default, with an on-host guard that disables `sshd` if exposure drifts

## What It Does

- runs remote native processes without a shell hop
- captures remote native process output as JSON with detected encodings and raw base64 stdout/stderr bytes
- runs remote Python scripts, including `conda run`
- sends PowerShell as UTF-8 base64 and decodes it on Windows before launching PowerShell
- ships a minimal stdio MCP server so agents can call structured tools instead of composing shell strings
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

For agent clients, prefer the MCP server so the model calls structured tools instead of authoring shell:

```bash
python3 ./windows-remote-executor/mcp/win_remote_mcp.py
```

Run PowerShell from a local file so the local shell never has to escape it:

```bash
./windows-remote-executor/bin/win-remote exec winbox --file ./scripts/check-host.ps1
cat ./scripts/check-host.ps1 | ./windows-remote-executor/bin/win-remote exec winbox --stdin
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
./windows-remote-executor/bin/win-remote wsl-sh winbox --cwd /tmp --file ./scripts/check-linux.sh -- --flag alpha
cat ./scripts/check-linux.sh | ./windows-remote-executor/bin/win-remote wsl-sh winbox --stdin -- --flag alpha
```

`wsl-sh` now stages the script through `scp`, copies it into a Linux-native temp path such as `/tmp/...` inside WSL, and executes it there. That avoids Windows command-line length failures and avoids accidentally running the script body straight from `/mnt/c/...`.

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

Hot-update the remote tool directory:

```bash
./windows-remote-executor/bin/win-remote update-tools winbox
```

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

When `access-policy.json` contains an access token hash, native commands such as `probe`, `run-b64`, `capture-b64`, `python-b64`, `powershell-b64`, the WSL commands, and `everything-b64` require the matching token. The wrapper automatically forwards `TARGET_ACCESS_TOKEN` as a base64 argument.

## Notes

- Remote paths should use forward slashes, for example `C:/CodexRemote/apps/myapp`.
- `probe`, `run`, `capture`, `py`, `exec`, `guard`, `repair`, and `policy` now prefer `C:/CodexRemote/tools/WindowsRemoteExecutor.cmd` and fall back to `C:/CodexRemote/tools/WindowsRemoteExecutor.Native.exe` when the launcher has not been installed yet.
- `repair` is the explicit self-heal path for `sshd` config, host keys, scoped firewall state, and service startup.
- Use `tasks` when you need scheduled-task state. It avoids the common `Get-ScheduledTaskInfo -TaskName ...` quoting failures around names with spaces.
- Use `wsl`, `wsl-capture`, and `wsl-sh` for Linux-side work inside WSL. They avoid the common `wsl.exe ... bash -lc ...` and `/mnt/c/...` quoting failures.
- `wsl.exe` under `run` is still fine for Windows-side WSL administration such as `--install`, `--set-default-version`, and `--shutdown`, but not for Linux-side workload launch.
- Keep long-lived models, caches, virtualenvs, and hot code on the WSL ext4 filesystem such as `/home/...`, not under `/mnt/c` or `/mnt/d`, or load times will collapse.
- If you update Windows-side files for a WSL workload, explicitly copy them into the WSL ext4 working tree before you trust the result. A changed `D:/...` tree does not automatically mean `/home/...` is updated.
- Inside WSL, prefer absolute executables for brittle dependencies. For example, use `/usr/lib/wsl/lib/nvidia-smi` for GPU queries and absolute venv interpreters such as `/home/sumie/amt_asr_wsl/.venv-vllm/bin/python` for workload entrypoints.
- Prefer `run` for human-facing command execution and progress logs.
- Prefer `capture` or `wsl-capture` when stdout/stderr may be UTF-16, locale-codepage, binary-adjacent, or otherwise too brittle for plain PTY parsing.
- On `X570`, treat `win-remote cmd` as unsupported. Prefer direct native executables through `run`.
- Legacy direct-over-SSH PowerShell fallback was removed. If PowerShell is needed, the native executor must be present.
- Treat raw `powershell.exe`, `pwsh`, and hand-rolled `-EncodedCommand` transport as unsupported. `run` and `capture` now block raw PowerShell by default; use `win-remote exec --file` or `--stdin` so the wrapper owns UTF-8/base64 encoding.
- Silent admin commands such as `put`, `get`, `deploy` without `--post`, `update-tools` without `--install-guard`, and `policy --no-run-guard` now print `OK` on success so agent clients do not misread silence as uncertainty.
- `find` still relies on an externally staged `es.exe`.
- The PowerShell route is now `local UTF-8 base64 -> WindowsRemoteExecutor.Native.exe powershell-b64 -> Windows-local decode -> PowerShell -EncodedCommand`, which removes one quoting layer from SSH.
