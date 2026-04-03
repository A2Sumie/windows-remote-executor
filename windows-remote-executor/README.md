# Windows Remote Executor

This toolkit lets a macOS or Linux shell drive a Windows machine over SSH without making PowerShell the primary transport. It is built for Codex and similar agentic tools that need reliable file transfer, native process launch, JSON probing, and a PowerShell fallback that does not depend on fragile local quoting.

The intended steady state is:

- `cmd.exe`, `scp`, and a native Windows executor for routine work
- PowerShell only when the task is specifically PowerShell-shaped, and only through the wrapper's UTF-8/base64 path
- SSH bound to a private address by default, with an on-host guard that disables `sshd` if exposure drifts

## What It Does

- runs remote `cmd.exe` commands
- runs remote native processes without a shell hop
- captures remote native process output as JSON with detected encodings and raw base64 stdout/stderr bytes
- runs remote Python scripts, including `conda run`
- sends PowerShell as UTF-8 base64 and decodes it on Windows before launching PowerShell
- uploads and downloads files with `scp`
- collects a JSON probe from the target host
- deploys a directory through a remote staging area
- installs an access policy with an optional access token hash
- installs an `sshd` guard that disables the service if it listens on an unexpected address
- installs `sshd` repair watch tasks and service recovery actions
- hot-updates the remote tool directory with backups

## Directory Layout

```text
windows-remote-executor/
├── bin/win-remote
├── lib/common.sh
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

Bootstrap prepares OpenSSH, writes `sshd_config`, scopes the firewall to the chosen local IP, installs authorized keys, creates `C:\CodexRemote\{tools,inbox,staging,apps,logs}`, and installs a visible `cmd.exe` recovery console path for local login sessions.

The recovery console is now launched by a highest-privilege `ONLOGON` scheduled task for the target user instead of a Startup-folder `RunAs` prompt. That removes the manual UAC click at sign-in while still giving the signed-in user a visible local console. A helper launcher remains in `C:\CodexRemote\tools\CodexRemote Console.cmd` and simply triggers the scheduled task on demand.

At logon the recovery console validates `sshd.exe -t`, retries service startup, and invokes `WindowsRemoteExecutor.Native.exe repair-sshd` automatically if the config is invalid or `sshd` still does not come up. The repair call is emitted through a separate helper script at `C:\CodexRemote\tools\codex-repair-sshd.cmd` so the startup batch stays simple and avoids `cmd.exe` parser edge cases.

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

Capture localized or byte-sensitive output as JSON:

```bash
./windows-remote-executor/bin/win-remote capture winbox wsl.exe --status
./windows-remote-executor/bin/win-remote capture winbox --out ./wsl-status.json wsl.exe --status
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

`update-tools` uploads the current native executable and companion files into `C:\CodexRemote\tools\`, keeping `.bak-<timestamp>` copies beside replaced files.

## Security Model

The guard logic is intentionally conservative.

- private mode accepts only standard private IPv4 ranges: `10.0.0.0/8`, `100.64.0.0/10`, `192.168.0.0/16`
- loopback and link-local are accepted for local recovery scenarios
- wildcard listeners such as `0.0.0.0` and `::` are treated as unsafe
- if `sshd` drifts away from the expected listen address, `guard-sshd` stops the service and changes startup to demand
- `sshd` is configured with Windows service failure restart actions and a repair watch scheduled task
- `public-with-token` is allowed only when the policy explicitly says so and an access token hash is configured
- the probe and guard output always surfaces the policy label, exposure mode, and whether a token is required

When `access-policy.json` contains an access token hash, native commands such as `probe`, `run-b64`, `capture-b64`, `python-b64`, `powershell-b64`, and `everything-b64` require the matching token. The wrapper automatically forwards `TARGET_ACCESS_TOKEN` as a base64 argument.

## Notes

- Remote paths should use forward slashes, for example `C:/CodexRemote/apps/myapp`.
- `probe`, `run`, `capture`, `py`, `exec`, `guard`, `repair`, and `policy` rely on `C:/CodexRemote/tools/WindowsRemoteExecutor.Native.exe`.
- `repair` is the explicit self-heal path for `sshd` config, host keys, scoped firewall state, and service startup.
- Prefer `run` for human-facing command execution and progress logs.
- Prefer `capture` when stdout/stderr may be UTF-16, locale-codepage, or binary-adjacent and you need stable JSON plus raw bytes.
- Legacy direct-over-SSH PowerShell fallback was removed. If PowerShell is needed, the native executor must be present.
- Treat raw `powershell.exe`, `pwsh`, and hand-rolled `-EncodedCommand` transport as unsupported. Use `win-remote exec --file` or `--stdin` so the wrapper owns UTF-8/base64 encoding.
- `find` still relies on an externally staged `es.exe`.
- The PowerShell route is now `local UTF-8 base64 -> WindowsRemoteExecutor.Native.exe powershell-b64 -> Windows-local decode -> PowerShell -EncodedCommand`, which removes one quoting layer from SSH.
