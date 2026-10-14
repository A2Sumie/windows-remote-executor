# WRE v6.1.2 deployment guide

Use the v6.1.2 Windows runtime/controller together with the onboarding helpers
attached to the [v6.1.2 Release](https://github.com/A2Sumie/windows-remote-executor/releases/tag/v6.1.2).
The onboarding flow below selects the real Tailscale address, initializes
pwsh 7, authorizes the controller public key, and installs a hash-only policy.

The controller's plain Token is saved automatically in a private env file.
Windows receives a bootstrap JSON containing the public key and Token SHA256.
WRE uses the bundled Python at `C:/WRE/wre`; PowerShell scripts run through
pwsh 7 with `-File` and structured arguments.

## Requirements

| Side | Requirements |
|---|---|
| Windows host | x64 Windows; Tailscale online; an enabled local SSH account in Administrators with an existing Windows profile; Administrator access for installation |
| Controller | macOS/Linux, Python 3.10+, Git, OpenSSH client tools and Tailscale CLI |
| PowerShell | pwsh 7 Core; Windows Terminal is optional; PSScriptAnalyzer is optional for local script lint |
| Network | Both devices are on the same authorized tailnet; TCP 22 is reachable |

The Windows package includes Python 3.12.10, pywin32 308 and comtypes 1.4.8.
The default installed entry is `C:/WRE/wre/rpc.py`. Requests and responses
remain UTF-8 JSON lines over the fixed SSH `rpc-stdio` entrypoint.

The assisted flow is for Tailscale IPv4 and local Windows accounts. Existing
domain-account or LAN-only installations can retain their configured SSH
authentication and use the release's native installer with an assigned
private address. Keep policy, apply spec and sshd listener aligned.

## 1. Identify the Windows device and initialize pwsh 7

On Windows, sign in once with the account that will use SSH. Record the actual
hostname and account name; the profile directory can have a different name:

```powershell
$env:COMPUTERNAME
$env:USERNAME
tailscale ip -4
```

The Tailscale IPv4 printed here is the address for this device. Avoid copying
an executable example LAN address. The initializer rejects a bootstrap whose
address is absent from this Windows host.

If pwsh 7 is missing, run this in an Administrator terminal:

```powershell
winget install --id Microsoft.PowerShell --exact --source winget --installer-type wix --silent --accept-package-agreements --accept-source-agreements
```

Select the MSI installer type for a machine-wide installation, including
future SYSTEM tasks. WinGet's default PowerShell 7.6 package is MSIX and is
user-scoped. If WinGet is unavailable, use the official
[PowerShell MSI instructions](https://learn.microsoft.com/en-us/powershell/scripting/install/installing-powershell-on-windows).
PowerShell 7 runs alongside Windows PowerShell 5.1.

Open a new pwsh 7 window and confirm:

```powershell
$PSVersionTable.PSVersion
$PSVersionTable.PSEdition
(Get-Process -Id $PID).Path
```

Expect version 7 or later and edition `Core`. A discovered WindowsApps alias
is suitable for that user; verify a machine-wide executable separately before
using it in a SYSTEM task.

## 2. Download and verify the onboarding helpers

The Release provides these additional assets:

| Asset | Purpose |
|---|---|
| `create-wre-target.py` | Save the private controller env and create hash-only Windows bootstrap JSON |
| `initialize-pwsh7.ps1` | Select the existing pwsh 7 Terminal profile; optionally install PSScriptAnalyzer |
| `initialize-wre.ps1` | Validate host/account/package, configure SSH/key/firewall/policy and verify installation |
| `ONBOARDING_SHA256SUMS.txt` | Checksums of the three helpers |

On the controller, use the matching controller checkout and download all
three helpers into its `onboarding` directory:

```bash
git clone --branch v6.1.2 --depth 1 https://github.com/A2Sumie/windows-remote-executor.git wre-controller-6.1.2
cd wre-controller-6.1.2
mkdir -p onboarding
wre_release=https://github.com/A2Sumie/windows-remote-executor/releases/download/v6.1.2
for wre_asset in create-wre-target.py initialize-pwsh7.ps1 initialize-wre.ps1 ONBOARDING_SHA256SUMS.txt; do
    curl -fL -o "onboarding/$wre_asset" "$wre_release/$wre_asset" || exit 1
done
python3 - <<'PY'
import hashlib
from pathlib import Path

directory = Path("onboarding")
expected_names = {"create-wre-target.py", "initialize-pwsh7.ps1", "initialize-wre.ps1"}
manifest = {}
for line in (directory / "ONBOARDING_SHA256SUMS.txt").read_text().splitlines():
    digest, name = line.split("  ", 1)
    if name not in expected_names or name in manifest:
        raise SystemExit("Unexpected onboarding manifest entry")
    manifest[name] = digest
if set(manifest) != expected_names:
    raise SystemExit("Incomplete onboarding manifest")
for name, expected in manifest.items():
    if hashlib.sha256((directory / name).read_bytes()).hexdigest() != expected:
        raise SystemExit("Checksum mismatch: " + name)
print("Onboarding checksums OK")
PY
```

Transfer the two verified `.ps1` files and their checksum file to a Windows
Downloads subdirectory named `wre-onboarding`. Taildrop can transfer them
before SSH is available. Alternatively download the same assets on Windows
and verify them with `Get-FileHash -Algorithm SHA256` before executing them.

In a normal pwsh 7 console for the Windows Terminal user:

```powershell
$wreOnboarding = Join-Path $env:USERPROFILE 'Downloads/wre-onboarding'
& "$wreOnboarding/initialize-pwsh7.ps1" -CheckOnly
& "$wreOnboarding/initialize-pwsh7.ps1"
```

The script backs up the original settings and changes only `defaultProfile`
to the existing enabled PowerShell Core profile. Open Windows Terminal once
after installing pwsh if the script reports no existing settings/profile.
New Terminal tabs use the selected profile; existing shells keep their runtime.
See the [Terminal default-profile reference](https://learn.microsoft.com/en-us/windows/terminal/customize-settings/startup).

For a macOS/Linux parser/lint workstation, install pwsh 7 using the official
[macOS](https://learn.microsoft.com/en-us/powershell/scripting/install/installing-powershell-on-macos)
or [Linux](https://learn.microsoft.com/en-us/powershell/scripting/install/installing-powershell-on-linux)
instructions, then initialize the analyzer:

```bash
pwsh -NoLogo -NoProfile -NonInteractive -File onboarding/initialize-pwsh7.ps1 -InstallAnalyzer
pwsh -NoLogo -NoProfile -NonInteractive -File onboarding/initialize-pwsh7.ps1 -InstallAnalyzer -CheckOnly
```

This initializes the native PowerShell parser runtime and PSScriptAnalyzer.
It does not change the workstation's login shell.

## 3. Create and save the controller credentials

Create an Ed25519 key, or use an existing key with its `.pub` file. Keep the
private key on the controller and load a passphrase-protected key into the
SSH agent:

```bash
ssh-keygen -t ed25519 -f "$HOME/.ssh/wre_win_lab" -C "WRE WIN-LAB"
ssh-add "$HOME/.ssh/wre_win_lab"
```

Set the real Windows hostname and login recorded in step 1. The following
prompts work in Bash and zsh:

```bash
printf 'Windows hostname from step 1: '
read -r wreDevice
printf 'Windows login from step 1: '
read -r wreUser
python3 onboarding/create-wre-target.py --name WIN-LAB --tailscale-device "$wreDevice" --user "$wreUser" --key "$HOME/.ssh/wre_win_lab"
```

The generator selects exactly one online Tailscale peer and saves:

```text
~/.config/wre/targets/WIN-LAB.env              private Token + SSH target, mode 0600
~/.config/wre/targets/WIN-LAB.bootstrap.json   public key + Token SHA256, mode 0600
```

The directory is mode 0700. The Token is never printed. Back up the private
env securely; it is the credential needed by the controller. The bootstrap
contains no plain Token or SSH private key.

Transfer only `WIN-LAB.bootstrap.json` to Windows, for example:

```bash
tailscale file cp "$HOME/.config/wre/targets/WIN-LAB.bootstrap.json" "$wreDevice:"
```

Use the recipient's Taildrop UI to save it into `Downloads/wre-onboarding`.
A successful `sent` acknowledgement and exit 0 confirm transfer, not execution.
If a same-name transfer is locked, resend with a fresh `--name` and pass that
actual filename to the initializer.

## 4. Download, verify and install WRE locally on Windows

Download and verify the original runtime ZIP in pwsh 7:

```powershell
$wreRelease = 'https://github.com/A2Sumie/windows-remote-executor/releases/download/v6.1.2'
$wreArchive = Join-Path $env:USERPROFILE 'Downloads/wre-6.1.2-windows-x64.zip'
$wrePackage = Join-Path $env:USERPROFILE 'Downloads/wre-6.1.2'
Invoke-WebRequest -Uri "$wreRelease/wre-6.1.2-windows-x64.zip" -OutFile $wreArchive
$wreExpected = '677a562326aa0d9ffb2a679c8f3f529884767bdc10db516bd64e305a421952f7'
if ((Get-FileHash -LiteralPath $wreArchive -Algorithm SHA256).Hash -ne $wreExpected) {
    throw 'WRE package checksum mismatch.'
}
Expand-Archive -LiteralPath $wreArchive -DestinationPath $wrePackage
```

Use a fresh extraction directory. GitHub's **Source code** archives provide
the controller source; the named Windows ZIP includes the embedded runtime.

Open **pwsh 7 as Administrator** on the Windows console. Keep this console
open so failures remain visible. Recreate the paths in that elevated session:

```powershell
$wreOnboarding = Join-Path $env:USERPROFILE 'Downloads/wre-onboarding'
$wrePackage = Join-Path $env:USERPROFILE 'Downloads/wre-6.1.2'
$wreConfig = Join-Path $wreOnboarding 'WIN-LAB.bootstrap.json'
& "$wreOnboarding/initialize-wre.ps1" -ConfigFile $wreConfig -PackageDirectory $wrePackage -CheckOnly
if ($LASTEXITCODE -ne 0) { throw 'Preflight failed; read the error before installing.' }
& "$wreOnboarding/initialize-wre.ps1" -ConfigFile $wreConfig -PackageDirectory $wrePackage
```

If local execution policy blocks a checksum-verified helper, use pwsh's
per-process `-ExecutionPolicy Bypass -File` options for that invocation.

The initializer:

1. Checks the host's assigned Tailscale IPv4, local account/profile, public
   key, Token hash and package selftest before changing configuration.
2. Installs missing Windows OpenSSH Server and uses NetSecurity to ensure a
   TCP 22 allow rule with the actual sshd executable path, the selected local
   address and remote scope `100.64.0.0/10`.
3. Reads effective `AuthorizedKeysFile` for the account and controller address,
   appends the public key idempotently, backs up existing files, and applies
   SID-based Administrators/SYSTEM ACLs; ordinary user key files also retain
   the account's own access.
4. Saves only the Token hash and aligns policy, apply spec and sshd listener.
   It invokes the release's native installer with `--keep-existing-policy` and
   preserves existing business task/apply-spec fields during reinstalls.
5. Grants the selected SSH account access to `C:/WRE`, starts a stopped sshd
   service or restarts a running one, and checks the actual listener and the
   four required enabled SYSTEM repair/apply tasks.

Expect `success: true`, phase `complete`, and the actual Tailscale listener.
Installation requires local Administrator access; SSH RPC runs as the selected
login user. **Treat the Token and the private target env as SYSTEM credentials**.
The package's original `install-wre.ps1` is also available for native/manual
installation; the assisted flow uses `initialize-wre.ps1` for these additional
checks and persistent error reports.

## 5. Verify key authentication and WRE from the controller

Verify the host-key fingerprint through the Windows console or another trusted
channel. Windows host public-key fingerprints can be read with:

```powershell
Get-ChildItem (Join-Path $env:ProgramData 'ssh/ssh_host_*_key.pub') | ForEach-Object {
    ssh-keygen.exe -lf $_.FullName
}
```

From the matching controller checkout created in step 2, use the saved env
file to authenticate through WRE's actual fixed entrypoint:

```bash
export WRE_TARGETS_DIR="$HOME/.config/wre/targets"
PYTHONPATH=. python3 -m v6.controller.shell WIN-LAB host.capabilities '{}'
PYTHONPATH=. python3 -m v6.controller.shell WIN-LAB --info
PYTHONPATH=. python3 -m v6.controller.shell WIN-LAB --probe
PYTHONPATH=. python3 -m v6.controller.shell WIN-LAB --guard
PYTHONPATH=. python3 -m v6.controller.shell WIN-LAB process.run '{"exe":"C:/WRE/wre/python/python.exe","args":["-I","-X","utf8","--version"]}'
PYTHONPATH=. python3 -m v6.controller.shell WIN-LAB host.tasks.list '{"prefix":"WRE","limit":20}'
```

`Permission denied (publickey)` is an SSH account/key/ACL problem and occurs
before the WRE Token check. `host.capabilities` needs SSH authentication but
is Token-exempt; `host.info`, probe and process execution must also succeed to
establish usable access.

Expect `ok: true`, protocol 6, build `v6.1`, policy status `ok`, configured
and active listeners exactly matching the target, and guard `exposureSafe: true`.
The bundled interpreter reports Python 3.12.10. A successful file transfer or
a listening port alone does not establish WRE authentication.

For PowerShell operations, use the discovered absolute pwsh executable and
stage a script through `file.writeText`, then run `-File` with an argv array.
Machine-wide installations normally use `C:/Program Files/PowerShell/7/pwsh.exe`.
The fixed Python RPC transport remains the WRE control entry.

The broader verifier also exercises jobs, files and WSL:

```bash
PYTHONPATH=. python3 -m v6.scripts.verify_v6_remote WIN-LAB
```

WSL is optional. A `wsl.list`/`not-found` result can make that matrix exit
nonzero; inspect the core results separately. The verifier creates temporary
inbox files/jobs, with task creation disabled by default.

## 6. Reinstall, retain credentials or reset a lost Token

Keep the current tree, policy, apply spec and sshd configuration in a private backup
location. Retain the old runtime ZIP and its checksum.

To regenerate a bootstrap while preserving the saved controller Token, run
the generator with the same target details and `--reuse`. If the existing env
is in `v6/targets`, point `--targets-dir` there or first copy it into your
private target directory with mode 0600:

```bash
python3 onboarding/create-wre-target.py --name WIN-LAB --tailscale-device "$wreDevice" --user "$wreUser" --key "$HOME/.ssh/wre_win_lab" --reuse
```

On the local elevated Windows console, retain the matching Token with:

```powershell
& "$wreOnboarding/initialize-wre.ps1" -ConfigFile $wreConfig -PackageDirectory $wrePackage -PreserveToken
```

`-PreserveToken` checks that the bootstrap hash matches the installed policy.
The initializer still updates the actual expected listener and apply spec.
Changing only sshd_config, or using the original `-KeepPolicy` with a new
`-Listen` argument, can leave self-repair pointing at the old policy address.

If the plain Token has been lost, explicitly run the generator with
`--rotate-token` in place of `--reuse`. It backs up the old private env and
stores a new secret before creating the hash-only bootstrap. Transfer that
bootstrap and run the Windows initializer without `-PreserveToken`, then
repeat step 5. This updates the host hash and controller credential together.

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
        "PYTHONPATH": "/absolute/path/wre-controller-6.1.2",
        "WRE_TARGETS_DIR": "/absolute/path/private-wre-targets"
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
| Original installer reports missing arguments on reinstall | Include `-Name` and `-Listen` together with `-KeepPolicy`; the assisted initializer takes a complete bootstrap instead |
| Listener reverts to an old LAN address | Align `expectedListenAddress`, apply spec and sshd_config; the SYSTEM repair task reads the policy |
| Firewall COM reports `The parameter is incorrect` | Use the assisted initializer's NetSecurity rule with an absolute sshd path; a stopped sshd also needs starting |
| Only `exit code 1` is visible | Run from a persistent local Administrator pwsh console and read the phase/error JSON and transcript |
| `Get-NetTCPConnection` finds no listener | Inspect sshd service state and startup type; a no-match query describes absence, not an installation diagnosis |
| Taildrop returns `Access denied` for a reused filename | Check the receiver's file/security state and resend with a fresh name; inspect final `sent`/exit0 before assuming delivery |
| pwsh 7 installed but Terminal opens 5.1 | Run `initialize-pwsh7.ps1` for the Terminal user, then open a new tab |
| `auth`, missing/corrupt/null-token policy | Match the controller's plain token to the installed hash; restore policy or reinstall on the Windows console |
| `rpc-stdio did not return JSON` | Check `C:/WRE/wre/python/python.exe` and `C:/WRE/wre/rpc.py`; read stderr and repair the installed tree |
| SFTP update reports missing build directory | Extract the verified release ZIP and set `--build-dir` to its `wre/` directory |
| Full verifier reports only WSL `not-found` | Record WSL as unavailable; check core WRE results separately |
| Transport breaks after a write action | Inspect the host/job/task state before deliberately retrying; a read-phase break may occur after the host has already acted |

Host logs: `C:/WRE/logs/rpc-audit.log`, `jobs-history.log`, `sshd-repair.log`
and `wre-apply.log`. The installer saves sshd configuration backups under
`C:/WRE/wre/backup`. Use these records and the controller stderr to diagnose
failures through the v6 entrypoint.

The assisted initializer also writes `C:/WRE/logs/onboarding-*.log` and
`onboarding-result-*.json`. Early/non-administrator failures can save the
JSON report in the current user's temporary directory; the console prints
the actual report path. Reports include the failed phase and error message,
with no plain Token or private key.

## Antivirus exceptions

For a reviewed WRE installation at the default root, its executable paths are:

```text
C:\WRE\wre\python\python.exe    RPC and process execution
C:\WRE\wre\python\pythonw.exe   SYSTEM repair/apply tasks
```

During installation, the package selftest uses the extracted package's
`wre/python/python.exe`. A security product that blocks that copy can prevent
preflight even when the installed interpreter is allowed. Review the named
executable/helper and its verified checksum in the product's quarantine or
exception UI. Manage exceptions explicitly; the helpers do not change
antivirus settings.

Microsoft references: [OpenSSH service installation](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse),
[key location and permissions](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_keymanagement),
[PowerShell installation](https://learn.microsoft.com/en-us/powershell/scripting/install/installing-powershell-on-windows).
