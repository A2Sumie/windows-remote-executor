# WRE v6.1.2 deployment guide

Install the Windows bootstrap package on the host, then use the matching
`v6.1.2` controller checkout on macOS or Linux. The default host entry is
`C:/WRE/wre/rpc.py`; requests and responses are UTF-8 JSON lines over SSH stdin
and stdout.

[Download v6.1.2](https://github.com/A2Sumie/windows-remote-executor/releases/tag/v6.1.2)
· [Protocol and actions](https://github.com/A2Sumie/windows-remote-executor/blob/v6.1.2/v6/V6.md)

## Requirements

| Side | Requirements |
|---|---|
| Windows host | x64 Windows with OpenSSH Server; an existing local or domain account for SSH; Administrator access for the first installation |
| Controller | macOS/Linux, Python 3.10+, Git and OpenSSH client tools (`ssh`, `scp`, `sftp`) |
| Network | The controller can reach the host's private LAN or Tailscale address on TCP 22 |

The bootstrap ZIP includes Python 3.12.10, pywin32 308 and comtypes 1.4.8.
These dependencies are pinned by SHA256 in the package builder and recorded in
`manifest.json`. The controller CLI uses the Python standard library; the
optional MCP frontend also needs the `mcp` package.

Examples use host alias `WIN-LAB`, Windows login `wreuser`, host address
`192.168.50.20` and controller address `192.168.50.10`. Replace those values
with your actual account and addresses. The examples assume the SSH account
is a member of the Windows Administrators group and can write `C:/WRE`.

## 1. Prepare OpenSSH on Windows

Open **Windows PowerShell as Administrator** on the host. Install and start
OpenSSH Server explicitly before running the WRE installer:

```powershell
$capability = Get-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0'
if ($capability.State -ne 'Installed') {
    Add-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0' | Out-Null
}
Set-Service -Name sshd -StartupType Automatic
Start-Service -Name sshd

$controllerAddress = '192.168.50.10'
$rule = Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue
if ($rule) {
    $rule | Set-NetFirewallRule -Enabled True -RemoteAddress $controllerAddress
} else {
    New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 -RemoteAddress $controllerAddress
}
```

Choose an address already assigned to the host for the installer's `-Listen`
argument. The installer pins `sshd_config` to that address and restarts sshd.
Using an address that is absent from the host can make SSH unreachable.

Microsoft documents the [OpenSSH installation and service setup](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse).

## 2. Set up SSH key authentication

On the **macOS/Linux controller**, create a key or use an existing one. For a
key protected by a passphrase, load it into your SSH agent:

```bash
ssh-keygen -t ed25519 -f "$HOME/.ssh/wre_win_lab" -C "WRE WIN-LAB"
ssh-add "$HOME/.ssh/wre_win_lab"
cat "$HOME/.ssh/wre_win_lab.pub"
```

Copy the public key line to the Windows host. For the administrator account
used in these examples, run this in **elevated Windows PowerShell**, replacing
the sample key with that line:

```powershell
$publicKey = 'ssh-ed25519 REPLACE_WITH_YOUR_PUBLIC_KEY WRE-WIN-LAB'
$keys = Join-Path $env:ProgramData 'ssh\administrators_authorized_keys'
Add-Content -LiteralPath $keys -Value $publicKey -Encoding ascii
icacls.exe $keys /inheritance:r /grant:r '*S-1-5-32-544:F' /grant:r '*S-1-5-18:F'
```

Check that the key file's ACL contains only Administrators and SYSTEM; review
any pre-existing explicit permissions. The SIDs above work on localized
Windows installations. A standard Windows account uses its own
`C:/Users/<user>/.ssh/authorized_keys` instead and needs appropriate access to
the WRE directories for file writes and updates. See Microsoft's
[key authentication guide](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_keymanagement).

From the controller, verify the host key fingerprint through the Windows
console or another trusted channel, then connect:

```bash
ssh -i "$HOME/.ssh/wre_win_lab" -p 22 wreuser@192.168.50.20
```

Exit that interactive session, then confirm unattended authentication works:

```bash
ssh -o BatchMode=yes -i "$HOME/.ssh/wre_win_lab" -p 22 wreuser@192.168.50.20 exit
```

WRE uses `BatchMode=yes`, so a password prompt or an unloaded key passphrase
will prevent controller calls. Keep the SSH private key on the controller.

## 3. Download, verify and install WRE on Windows

Use a normal **Windows PowerShell** window. Download the bootstrap ZIP into a
user-writable directory and check its checksum before extraction:

```powershell
$release = 'https://github.com/A2Sumie/windows-remote-executor/releases/download/v6.1.2'
$archive = Join-Path $env:USERPROFILE 'Downloads\wre-6.1.2-windows-x64.zip'
$package = Join-Path $env:USERPROFILE 'Downloads\wre-6.1.2'
Invoke-WebRequest -Uri "$release/wre-6.1.2-windows-x64.zip" -OutFile $archive
$expected = '677a562326aa0d9ffb2a679c8f3f529884767bdc10db516bd64e305a421952f7'
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expected) {
    throw 'WRE package checksum mismatch; stop before installing.'
}
Expand-Archive -LiteralPath $archive -DestinationPath $package
```

Use a new extraction directory if that destination already exists. Select
`wre-6.1.2-windows-x64.zip` in the release assets: GitHub's automatic
**Source code** archives are for the controller/source checkout and do not
include the embedded Windows runtime.

The extracted root contains:

```text
deploy-wre.py
install-wre.ps1
manifest.json
wre/
  rpc.py
  actions/
  win32/
  python/python.exe
  python/pythonw.exe
```

Generate a random token in that same PowerShell session:

```powershell
$tokenBytes = New-Object byte[] 32
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($tokenBytes)
$rng.Dispose()
$token = ([BitConverter]::ToString($tokenBytes)).Replace('-', '').ToLowerInvariant()
```

Save the value of `$token` in your password manager for the controller's
`TARGET_ACCESS_TOKEN`. Then install, keeping the alias and listener explicit:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$package\install-wre.ps1" -Name 'WIN-LAB' -Listen '192.168.50.20' -Token $token
```

Approve the UAC elevation request. Keep the entire extracted package together;
the wrapper locates `deploy-wre.py` and `wre/python/python.exe` relative to
itself. It works from paths with spaces or Unicode characters.

The installer selftests the package, creates `C:/WRE/wre`, copies the runtime
and source, writes the token hash to `access-policy.json`, backs up and pins
`sshd_config`, and registers the `WRE Sshd Repair *` and `WRE WRE Apply`
scheduled tasks. Check the console output for task-registration errors even
if the installer exits successfully.

The SSH login user runs RPC/process/file operations. Repair and apply-agent
tasks run as SYSTEM. **Treat the WRE token as a SYSTEM credential**: a token
holder can use the privileged task lanes. Keep it and the SSH key out of Git.
Missing, corrupt or null-token policies fail closed; only
`host.capabilities` remains available without valid authentication.

## 4. Configure the macOS/Linux controller

Use the same release tag as the Windows package:

```bash
git clone --branch v6.1.2 --depth 1 https://github.com/A2Sumie/windows-remote-executor.git wre-controller-6.1.2
cd wre-controller-6.1.2
cp v6/targets/example.env v6/targets/WIN-LAB.env
chmod 600 v6/targets/WIN-LAB.env
```

Edit `v6/targets/WIN-LAB.env` with these fields:

```dotenv
TARGET_NAME=WIN-LAB
TARGET_HOST=192.168.50.20
TARGET_USER=wreuser
TARGET_PORT=22
TARGET_KEY=/Users/you/.ssh/wre_win_lab
TARGET_EXPECTED_LISTEN_ADDRESS=192.168.50.20
TARGET_EXPOSURE_MODE=private-only
TARGET_ACCESS_TOKEN=REPLACE_WITH_TOKEN_FROM_INSTALL
```

Set `TARGET_KEY` to the real absolute path on the controller, and set
`TARGET_ACCESS_TOKEN` to the **plain token**, matching `$token` from installation.
Do not put the SHA256 hash in this field. Real target `.env` files are ignored
by Git in the released checkout. The loader also accepts an env file's path
directly, or a directory supplied through `WRE_TARGETS_DIR`.

## 5. Verify the installation

Run these from the controller checkout root:

```bash
PYTHONPATH=. python3 -m v6.controller.shell WIN-LAB --info
PYTHONPATH=. python3 -m v6.controller.shell WIN-LAB --probe
PYTHONPATH=. python3 -m v6.controller.shell WIN-LAB --guard
PYTHONPATH=. python3 -m v6.controller.shell WIN-LAB process.run '{"exe":"C:/WRE/wre/python/python.exe","args":["--version"]}'
PYTHONPATH=. python3 -m v6.controller.shell WIN-LAB file.mkdir '{"path":"C:/WRE/inbox"}'
PYTHONPATH=. python3 -m v6.controller.shell WIN-LAB host.tasks.list '{"prefix":"WRE","verbose":true}'
```

Check for `ok: true`, `protocolVersion: 6`, build `v6.1`, a valid policy and
`wreRoot: C:/WRE`. The process smoke should return `exitCode: 0` and the bundled
Python version. The task list should include enabled repair tasks and the
SYSTEM apply task. `--guard` uses `noDisable: true` for inspection; investigate
any unsafe or unexpected listener addresses it reports.

For the broader process/job/file/WSL matrix:

```bash
PYTHONPATH=. python3 -m v6.scripts.verify_v6_remote WIN-LAB
```

The verifier creates temporary inbox files and process jobs. Task creation is
disabled by default. It also calls `wsl.list`: a host without the Windows WSL
component can return `not-found` and make this matrix exit nonzero. WSL is
optional for core WRE; record that specific gap and still inspect every other
result. A host with WSL but no distributions skips the WSL execution smoke.

## 6. Update an existing host

Before changing an installation, keep a copy of its current `wre` tree,
`access-policy.json` and sshd configuration in a private backup location.
Retain the previous release package and its checksum for rollback.

### On-host reinstall

Download and verify the selected package, extract it to a new directory, and
preserve the existing token policy with **all required arguments**:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$package\install-wre.ps1" -Name 'WIN-LAB' -Listen '192.168.50.20' -KeepPolicy
```

`-KeepPolicy` requires a valid existing `C:/WRE/wre/access-policy.json`.
Supply `-Name` and `-Listen` on reinstalls too: the Python installer requires
both even though the wrapper's parameter declarations mark them optional.
To rotate the token, use `-Token $newToken` and update the controller env file.

### Controller update without elevation

Use a verified **release ZIP** as the upload source. In the released controller
checkout on macOS/Linux with `curl` and `unzip`, download and extract v6.1.2,
then pass its `wre/` directory explicitly. The subshell stops on a failed
download, checksum or update:

```bash
(
set -eu
wre_stage_dir=$(mktemp -d "${TMPDIR:-/tmp}/wre-6.1.2.XXXXXX")
curl -fL -o "$wre_stage_dir/wre-6.1.2-windows-x64.zip" https://github.com/A2Sumie/windows-remote-executor/releases/download/v6.1.2/wre-6.1.2-windows-x64.zip
python3 - "$wre_stage_dir/wre-6.1.2-windows-x64.zip" <<'PY'
import hashlib
import sys
from pathlib import Path

actual = hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest()
expected = "677a562326aa0d9ffb2a679c8f3f529884767bdc10db516bd64e305a421952f7"
if actual != expected:
    raise SystemExit(f"SHA256 mismatch: {actual}")
print("SHA256 OK")
PY
unzip "$wre_stage_dir/wre-6.1.2-windows-x64.zip" -d "$wre_stage_dir/package"
PYTHONPATH=. python3 -m v6.controller.shell WIN-LAB file.mkdir '{"path":"C:/WRE/inbox"}'
PYTHONPATH=. python3 -m v6.scripts.deploy_sftp WIN-LAB --build-dir "$wre_stage_dir/package/wre"
)
```

The staging directory is created fresh for each update. The explicit
`--build-dir` selects the release contents instead of the script's old
`6.0.0` build-directory default. `file.mkdir` prepares the inbox before the
upload. This path requires working SSH/SFTP, Windows `tar.exe`, write access
to `C:/WRE`, and the existing elevated bootstrap tasks.

The updater resolves the token hash from `--access-token`, then the target's
`TARGET_ACCESS_TOKEN`, then the live policy. It aborts if no valid token source
is available. It rewrites a private-only policy using the target env's
`TARGET_EXPECTED_LISTEN_ADDRESS`. Repeat the verification in step 5 after
every update.

Leave `ForceCommand` off for this workflow: restricting the SSH account to
RPC disables the SFTP/SCP and remote extraction operations used by the updater.

### Rollback

Download and checksum-verify the previously working release package. On the
Windows console, run its installer with the same `-Name`, `-Listen` and
`-KeepPolicy` arguments, then repeat step 5 using its matching controller tag.
If the policy was damaged, restore the saved `access-policy.json` before the
reinstall. If SSH is unreachable, use the Windows console to restore the saved
`sshd_config` and restart `sshd`; keep local console access until verification
passes.

## Optional: connect an MCP client

Create a controller virtual environment and install the MCP dependency:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install mcp
```

Configure your MCP client to launch the following stdio server, replacing both
absolute paths with your released checkout location:

```json
{
  "mcpServers": {
    "wre": {
      "command": "/absolute/path/wre-controller-6.1.2/.venv/bin/python",
      "args": ["-m", "v6.mcp.wre_mcp"],
      "env": {
        "PYTHONPATH": "/absolute/path/wre-controller-6.1.2"
      }
    }
  }
}
```

This shows the common `mcpServers` shape; use your client's own configuration
schema. Keep tokens in the target env files. Tools such as `wre_info`,
`wre_run`, `wre_read`, `wre_write` and `wre_tasks` take structured arguments.

The `wre_find` family additionally needs a separately deployed
`C:/WRE/tmp/wreq.exe`. That executable is outside the bootstrap package; use
`wre_search` for a bounded filesystem walk when it is absent.

## Troubleshooting

| Symptom | Check or action |
|---|---|
| SSH refuses or times out | Confirm the host has the `-Listen` IP, sshd is running, the firewall permits the controller, and TCP 22 is reachable |
| `Permission denied (publickey)` | Check the SSH user, public-key location/ACL, controller private-key path and loaded SSH agent |
| Installer reports missing arguments on reinstall | Include `-Name` and `-Listen` together with `-KeepPolicy` |
| `auth`, missing/corrupt/null-token policy | Match the controller's plain token to the installed hash; restore policy or reinstall on the Windows console |
| `rpc-stdio did not return JSON` | Check `C:/WRE/wre/python/python.exe` and `C:/WRE/wre/rpc.py`; read stderr and repair the installed tree |
| SFTP update reports missing build directory | Extract the verified release ZIP and set `--build-dir` to its `wre/` directory |
| Full verifier reports only WSL `not-found` | Record WSL as unavailable; check core WRE results separately |
| Transport breaks after a write action | Inspect the host/job/task state before deliberately retrying; a read-phase break may occur after the host has already acted |

Host logs: `C:/WRE/logs/rpc-audit.log`, `jobs-history.log`, `sshd-repair.log`
and `wre-apply.log`. The installer saves sshd configuration backups under
`C:/WRE/wre/backup`. Use these records and the controller stderr to diagnose
failures through the v6 entrypoint.
