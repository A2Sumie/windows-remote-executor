# Windows Remote Executor

This toolkit lets a macOS or Linux host drive a Windows machine over SSH without making shell text the primary transport. It is built for Codex and similar agentic tools that need reliable file transfer, native process launch, JSON probing, WSL execution, and staged script execution that does not depend on fragile local quoting.

For agentic clients, the preferred entrypoint is the structured MCP server in `MCP.md`. The MCP server and the `win-remote` CLI both use the native V3 `rpc-stdio` protocol: a fixed remote command, one UTF-8 JSON request line on stdin, and one UTF-8 JSON response line on stdout.

This is a hard stability boundary: if a task can be expressed as argv, script body, file upload/download, WSL program, scheduled-task query, or policy/guard operation, use V3. Avoid raw `cmd.exe`, raw `powershell.exe`, `wsl.exe ... bash -lc ...`, or hand-built Windows command strings in agent work.

The intended steady state is:

- direct native process launch, staged `scp`, and a native C# Windows executor for routine work
- V3 `rpc-stdio` stdin JSON for all remote control-plane operations
- PowerShell or cmd script control only through V3 `script.run` / `script.capture`
- SSH bound to a private address by default, with an on-host guard that disables `sshd` if exposure drifts

## What It Does

- runs remote native processes without a shell hop
- captures remote process output as JSON with detected encodings and raw base64 stdout/stderr bytes
- starts Windows resident/background processes with structured argv and stdout/stderr file paths
- runs remote Windows Python scripts, including explicit Python and conda options
- sends PowerShell/cmd script bodies through V3 JSON before the native executor stages and runs them
- ships a minimal stdio MCP server so agents can call structured tools instead of composing shell strings
- runs WSL programs and WSL shell scripts with structured distro/user/cwd arguments
- runs WSL Python scripts/modules through explicit Python executable, cwd, and argv
- launches WSL resident services with PID, port, health, and log readiness diagnostics
- uploads and downloads files with `scp`, using V3 for remote directory setup and post-steps
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
├── lib/
│   ├── win_remote_cli.py
│   └── wre_v3_client.py
├── mcp/
│   └── win_remote_mcp.py
├── scripts/
│   ├── make-bootstrap-package.sh
│   ├── verify-remote-cases.sh
│   └── verify-v3-remote-cases.sh
├── targets/example.env
└── windows/
    ├── bootstrap-host.ps1
    ├── bootstrap-x570.ps1
    ├── install-openssh-executor.ps1
    └── install-wre-new-host.ps1
```

`bootstrap-x570.ps1` is a compatibility wrapper around the generic `bootstrap-host.ps1`.

## Bootstrap the Windows Host

For a new desktop-only Windows machine, use a generated bootstrap package. From the controller, build the package from a native release-shaped asset:

```bash
./windows-remote-executor/scripts/make-bootstrap-package.sh \
  --native-zip /tmp/wre-release/windows-remote-executor-native-vX.Y.Z-scd-win-x64.zip \
  --public-key ~/.ssh/id_ed25519.pub \
  --target-name winbox-new \
  --generate-token
```

For a brand-new machine that may not have the .NET 8 runtime, prefer the self-contained `scd-win-x64` asset; it bundles the runtime so the native executor runs without a separate .NET install. The framework-dependent `fdd-win-x64` asset is also accepted when .NET 8 is already present.

Copy the generated zip to the Windows desktop, extract it, open elevated PowerShell, and run:

```powershell
cd <extracted package folder>
.\deploy-wre-v3.ps1
```

The deployment wrapper sets the process execution policy for the current process, checks elevation, validates the packaged key/native payload, and calls `install-wre-new-host.ps1`. The installer writes a `target-*.env` template beside itself and under `C:\CodexRemote\logs` for the controller.

If you are manually bootstrapping from a source checkout on the Windows machine, run the PowerShell bootstrap script from an elevated PowerShell session:

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

Bootstrap prepares OpenSSH, writes `sshd_config`, scopes the firewall to the chosen local IP, installs authorized keys, creates `C:\CodexRemote\{tools,inbox,staging,apps,logs}`, removes old `cmd` recovery artifacts, and installs headless `repair-sshd` scheduled tasks that invoke the stable `WindowsRemoteExecutor.cmd` launcher.

The logon/startup repair path is headless. There is no Startup-folder batch file, no `cmd.exe` recovery window, and no `RunAs` prompt at sign-in. Three scheduled tasks cover the steady state:

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

Run PowerShell from a local file or stdin so the local shell never has to escape it:

```bash
./windows-remote-executor/bin/win-remote exec winbox --file ./scripts/check-host.ps1
cat ./scripts/check-host.ps1 | ./windows-remote-executor/bin/win-remote exec winbox --stdin
./windows-remote-executor/bin/win-remote exec-capture winbox --out ./ps-state.json --stdin < ./scripts/check-host.ps1
```

Run native programs and Python without a shell hop:

```bash
./windows-remote-executor/bin/win-remote run winbox --cwd C:/CodexRemote/inbox whoami.exe
./windows-remote-executor/bin/win-remote capture winbox --out ./whoami.json whoami.exe /user
./windows-remote-executor/bin/win-remote py winbox C:/CodexRemote/inbox/echo_args.py --cwd C:/CodexRemote/inbox -- --plain "alpha beta"
```

Run Linux programs and shell scripts inside WSL without composing `wsl.exe ... bash -lc ...`:

```bash
./windows-remote-executor/bin/win-remote wsl winbox --cwd /tmp /usr/bin/whoami
./windows-remote-executor/bin/win-remote wsl-capture winbox --out ./wsl-uname.json /usr/bin/uname -a
./windows-remote-executor/bin/win-remote wsl-py-capture winbox --cwd /home/sumie/app --python /home/sumie/app/.venv/bin/python --out ./py-version.json --module platform
./windows-remote-executor/bin/win-remote wsl-py winbox --cwd /home/sumie/app --python /home/sumie/app/.venv/bin/python scripts/task.py -- --input data.json
./windows-remote-executor/bin/win-remote wsl-sh winbox --cwd /tmp --file ./scripts/check-linux.sh -- --flag alpha
cat ./scripts/check-linux.sh | ./windows-remote-executor/bin/win-remote wsl-sh winbox --stdin -- --flag alpha
cat ./scripts/run-server.sh | ./windows-remote-executor/bin/win-remote wsl-resident winbox --cwd /home/sumie/app --log-file /home/sumie/app/logs/server.log --pid-file /home/sumie/app/run/server.pid --port 8023 --stdin
```

For WSL Python work, prefer `wsl-py` / `wsl-py-capture` over shell activation chains. Pass an absolute interpreter such as `/home/.../.venv/bin/python`, a Linux cwd, a module or script path, and script args after `--`. This keeps Python environment selection explicit and avoids multi-layer quoting around `source`, `conda activate`, `python -c`, and backticks.

For long-lived WSL services, prefer `wsl-resident` over repo-local `tmux` or `nohup` policy. It stages the script, copies it into WSL temp space, launches it in a detached session, and returns structured JSON with PID, readiness status, listener snapshot, and recent log lines.

Deploy a directory and optionally run a post-step through V3 `script.run`:

```bash
./windows-remote-executor/bin/win-remote deploy winbox ./dist C:/CodexRemote/apps/myapp \
  --post-file ./scripts/deploy-post.ps1
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

Validate a V3 target:

```bash
./windows-remote-executor/scripts/verify-v3-remote-cases.sh winbox
./windows-remote-executor/scripts/verify-remote-cases.sh winbox
```

`verify-v3-remote-cases.sh` exercises the native V3 action surface. `verify-remote-cases.sh` exercises the public CLI wrapper behavior such as path guards, file transfer, script routes, and optional WSL cases.

Local publish directories are for development verification only. Production remote executor updates should use GitHub release assets.

Inspect scheduled tasks without hand-writing PowerShell quoting:

```bash
./windows-remote-executor/bin/win-remote tasks winbox
./windows-remote-executor/bin/win-remote tasks winbox --task-name "CodexRemote Sshd Repair Watch"
```

`update-tools` uploads the current native payload into `C:\CodexRemote\tools\releases\<timestamp>\`, refreshes `C:\CodexRemote\tools\WindowsRemoteExecutor.cmd`, and writes `C:\CodexRemote\tools\current-release.txt`. That lets the control plane flip to a new release even when older executor processes are still running.

## Security Model

The guard logic is intentionally conservative.

- private mode accepts only standard private IPv4 ranges: `10.0.0.0/8`, `100.64.0.0/10`, `192.168.0.0/16`
- loopback and link-local are accepted for local recovery scenarios
- wildcard listeners such as `0.0.0.0` and `::` are treated as unsafe
- if `sshd` drifts away from the expected listen address, `guard-sshd` stops the service and changes startup to demand
- `sshd` is configured with Windows service failure restart actions and a repair watch scheduled task
- `public-with-token` is allowed only when the policy explicitly says so and an access token hash is configured
- the probe and guard output always surfaces the policy label, exposure mode, and whether a token is required

When `access-policy.json` contains an access token hash, protected V3 actions require the matching `TARGET_ACCESS_TOKEN`. The wrapper automatically forwards the token in the V3 JSON request.

On `policy --command-mode argv-only`, native policy rejects shell/interpreter executables through `run`, `capture`, and `spawn`; V3 script actions remain available for staged maintenance.

## Notes

- Remote paths should use forward slashes, for example `C:/CodexRemote/apps/myapp`.
- A drive-relative path such as `D:StreamServauto_stream.py` is rejected locally because it usually means the caller forgot to quote `D:\StreamServ\auto_stream.py` and the local shell stripped backslashes.
- `probe`, `run`, `capture`, `py`, `exec`, `guard`, `repair`, and `policy` prefer `C:/CodexRemote/tools/WindowsRemoteExecutor.cmd` and fall back to `C:/CodexRemote/tools/WindowsRemoteExecutor.Native.exe` when the launcher has not been installed yet.
- `repair` is the explicit self-heal path for `sshd` config, host keys, scoped firewall state, and service startup.
- Use `tasks` when you need scheduled-task state. It avoids the common `Get-ScheduledTaskInfo -TaskName ...` quoting failures around names with spaces.
- Use `wsl`, `wsl-capture`, `wsl-sh`, and `wsl-resident` for Linux-side work inside WSL. They avoid the common `wsl.exe ... bash -lc ...` and `/mnt/c/...` quoting failures.
- Use `wsl-py` and `wsl-py-capture` for WSL Python venv/conda/model environments. Prefer absolute Python paths and argv over activation strings.
- `wsl.exe` under `run` is still fine for Windows-side WSL administration such as `--install`, `--set-default-version`, and `--shutdown`, but not for Linux-side workload launch.
- Keep long-lived models, caches, virtualenvs, and hot code on the WSL ext4 filesystem such as `/home/...`, not under `/mnt/c` or `/mnt/d`, or load times will collapse.
- If you update Windows-side files for a WSL workload, explicitly copy them into the WSL ext4 working tree before you trust the result. A changed `D:/...` tree does not automatically mean `/home/...` is updated.
- Inside WSL, prefer absolute executables for brittle dependencies. For example, use `/usr/lib/wsl/lib/nvidia-smi` for GPU queries and absolute venv interpreters such as `/home/sumie/amt_asr_wsl/.venv-vllm/bin/python` for workload entrypoints.
- Do not turn WSL Python work into nested `bash -lc`, command substitution, or backtick-heavy inline Python when `wsl-py` can carry the same operation as argv.
- Prefer `run` for human-facing command execution and progress logs.
- Prefer `capture` or `wsl-capture` when stdout/stderr may be UTF-16, locale-codepage, binary-adjacent, or otherwise too brittle for plain PTY parsing.
- Prefer `spawn` for Windows-side resident/background processes. It returns a launch receipt; verify the expected process, log, port, or output file separately.
- On `X570`, prefer direct native executables through `run` for argv-shaped work. For shell-shaped work, use staged `exec --shell powershell` or `exec --shell cmd`; `win-remote cmd` is a compatibility wrapper over the same V3 cmd script action.
- Raw PowerShell over direct SSH is not part of the supported path. If PowerShell is needed, use V3 `script.run` / `script.capture` through `win-remote exec` or MCP.
- `run`, `capture`, and `spawn` have a default guard for raw `powershell.exe` / `pwsh`; `--allow-powershell` and `WIN_REMOTE_ALLOW_RAW_POWERSHELL=1` are explicit escape hatches. Prefer staged `exec --file` or `--stdin` when it reduces quoting and encoding risk.
- Silent admin commands such as `put`, `get`, `deploy` without `--post`, `update-tools`, and `policy --no-run-guard` print `OK` on success so agent clients do not misread silence as uncertainty.
- `find` relies on an externally staged `es.exe` and the Everything service.
