# WRE v6.1.2 — Windows bootstrap package

Operate a Windows host from macOS/Linux through structured JSON RPC over SSH.
This release packages the v6.1 runtime and the self-elevating Windows installer.

**Start here:** [Full deployment guide](https://github.com/A2Sumie/windows-remote-executor/blob/main/DEPLOYMENT.md)
· [Controller source at v6.1.2](https://github.com/A2Sumie/windows-remote-executor/tree/v6.1.2)
· [Protocol and action reference](https://github.com/A2Sumie/windows-remote-executor/blob/v6.1.2/v6/V6.md)

## Downloads and integrity

| Asset | Purpose |
|---|---|
| [wre-6.1.2-windows-x64.zip](https://github.com/A2Sumie/windows-remote-executor/releases/download/v6.1.2/wre-6.1.2-windows-x64.zip) | Windows bootstrap package, 18,247,093 bytes |
| [SHA256SUMS.txt](https://github.com/A2Sumie/windows-remote-executor/releases/download/v6.1.2/SHA256SUMS.txt) | Package checksum |
| [DEPLOYMENT.md](https://github.com/A2Sumie/windows-remote-executor/releases/download/v6.1.2/DEPLOYMENT.md) | Downloadable deployment guide |

```text
677a562326aa0d9ffb2a679c8f3f529884767bdc10db516bd64e305a421952f7  wre-6.1.2-windows-x64.zip
```

The bootstrap package contains Python 3.12.10, pywin32 308 and comtypes 1.4.8,
with pinned dependency hashes in `manifest.json`. Source tag: `v6.1.2`, commit
[`77251cd`](https://github.com/A2Sumie/windows-remote-executor/commit/77251cd).
Use the named Windows ZIP for installation; GitHub's automatic **Source code**
archives contain the controller/source tree.

## What changed since v6.1.1

- **Transport failure handling:** read-phase SSH breaks surface without an
  automatic resend, so a processed write is not silently duplicated.
  Provably undelivered write-phase failures reconnect and retry once.
- **MCP resilience:** tool exceptions are contained, quick tools have bounded
  deadlines, and the server supplies agent-facing instructions.
- **Path and argument handling:** Explorer quote pairs and doubled path
  backslashes are normalized; suspicious shell arguments receive a
  `quotingNote`.
- **v3 retirement:** the old v3 trees were removed from this tag; controller
  target files now live in `v6/targets/`, and deployment token resolution uses
  the explicit token, target env or live policy.
- **Verification:** the release source includes the v6 unit/integration and
  chaos suites.

## New Windows host

First prepare OpenSSH Server, a reachable private listener address and SSH
key authentication. The [deployment guide](https://github.com/A2Sumie/windows-remote-executor/blob/main/DEPLOYMENT.md)
includes the Windows and controller commands for those prerequisites.

In **Windows PowerShell**, after downloading the ZIP:

```powershell
$archive = Join-Path $env:USERPROFILE 'Downloads\wre-6.1.2-windows-x64.zip'
$package = Join-Path $env:USERPROFILE 'Downloads\wre-6.1.2'
$expected = '677a562326aa0d9ffb2a679c8f3f529884767bdc10db516bd64e305a421952f7'
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expected) {
    throw 'WRE package checksum mismatch; stop before installing.'
}
Expand-Archive -LiteralPath $archive -DestinationPath $package

$tokenBytes = New-Object byte[] 32
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($tokenBytes)
$rng.Dispose()
$token = ([BitConverter]::ToString($tokenBytes)).Replace('-', '').ToLowerInvariant()

# Replace the alias and listener IP; save $token securely for the controller.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$package\install-wre.ps1" -Name 'WIN-LAB' -Listen '192.168.50.20' -Token $token
```

Extract to a user-writable directory, keep the package together, and approve
the UAC request. The installer creates `C:/WRE/wre`, writes the token hash,
pins sshd's listener and registers repair/apply tasks. Check its output for
errors before continuing.

## Controller setup and smoke test

On **macOS/Linux**, use the matching tag:

```bash
git clone --branch v6.1.2 --depth 1 https://github.com/A2Sumie/windows-remote-executor.git wre-controller-6.1.2
cd wre-controller-6.1.2
cp v6/targets/example.env v6/targets/WIN-LAB.env
chmod 600 v6/targets/WIN-LAB.env
```

Edit the env file with the SSH host/user/port, absolute private-key path,
expected listener address and `TARGET_ACCESS_TOKEN` set to the **plain token**
from installation. Real target env files are gitignored. Then run:

```bash
PYTHONPATH=. python3 -m v6.controller.shell WIN-LAB --info
PYTHONPATH=. python3 -m v6.controller.shell WIN-LAB --probe
PYTHONPATH=. python3 -m v6.controller.shell WIN-LAB --guard
PYTHONPATH=. python3 -m v6.controller.shell WIN-LAB process.run '{"exe":"C:/WRE/wre/python/python.exe","args":["--version"]}'
```

Expect successful RPC responses, `protocolVersion: 6`, build `v6.1`, a valid
policy and process `exitCode: 0`. The full
`python3 -m v6.scripts.verify_v6_remote WIN-LAB` matrix additionally exercises
jobs, files and WSL. WSL is optional: an absent WSL component can make that
matrix exit nonzero with `wsl.list`/`not-found`; inspect the core results too.

## Existing host: preserve the token

After downloading, verifying and extracting the package, reinstall on Windows
with **all three arguments**:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$package\install-wre.ps1" -Name 'WIN-LAB' -Listen '192.168.50.20' -KeepPolicy
```

The controller update route must select the extracted release directory:

```bash
PYTHONPATH=. python3 -m v6.controller.shell WIN-LAB file.mkdir '{"path":"C:/WRE/inbox"}'
PYTHONPATH=. python3 -m v6.scripts.deploy_sftp WIN-LAB --build-dir /absolute/path/to/extracted-package/wre
```

See the [guide](https://github.com/A2Sumie/windows-remote-executor/blob/main/DEPLOYMENT.md)
for backup, rollback, MCP setup and troubleshooting. Treat the WRE token as a
SYSTEM credential. The `wre_find` family needs the separately deployed
`C:/WRE/tmp/wreq.exe`; use `wre_search` when it is absent.
