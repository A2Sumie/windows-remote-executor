# install-wre.ps1 — one-command on-host installer for the WRE v6 bootstrap package.
#
# Wraps deploy-wre.py: self-elevates, locates the package next to itself,
# validates token args, and drives the embedded python. No prerequisites —
# the package carries its own CPython.
#
# Usage (from ANY shell; elevation is automatic):
#   powershell -ExecutionPolicy Bypass -File .\install-wre.ps1 ^
#       -Name <host-alias> -Listen <ip-or-empty> -Token <random-long-secret>
#   # re-install over an existing tree without retyping the token:
#   powershell -ExecutionPolicy Bypass -File .\install-wre.ps1 -KeepPolicy
#
# The script must sit in the SAME directory as the extracted zip contents
# (it resolves deploy-wre.py relative to its own location), which is exactly
# how the bootstrap zip lays files out.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)] [string]$Name,
    [Parameter(Mandatory = $false)] [string]$Listen,
    [Parameter(Mandatory = $false)] [string]$Token,
    [Parameter(Mandatory = $false)] [switch]$KeepPolicy,
    [Parameter(Mandatory = $false)] [switch]$NoSelfElevate
)

$ErrorActionPreference = 'Stop'

# --- locate the package (script's own directory) ---
$pkgDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$deployScript = Join-Path $pkgDir 'deploy-wre.py'
$pythonExe = Join-Path $pkgDir 'wre\python\python.exe'
foreach ($f in @($deployScript, $pythonExe)) {
    if (-not (Test-Path $f)) {
        Write-Error "package file missing: $f — run this script from the extracted zip root"
        exit 2
    }
}

# --- validate arg contract (mirror deploy-wre.py's exactly-one rule) ---
if (-not $KeepPolicy) {
    if ([string]::IsNullOrWhiteSpace($Token)) {
        Write-Error "provide -Token <random-long-secret> (its sha256 is stored, never the plain text) or use -KeepPolicy"
        exit 2
    }
    if ([string]::IsNullOrWhiteSpace($Name)) {
        Write-Error "provide -Name <host-alias> for the repair-task names / apply spec"
        exit 2
    }
}

# --- self-elevate ---
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin -and -not $NoSelfElevate) {
    Write-Host "[install-wre] relaunching elevated..."
    $argList = @('-ExecutionPolicy', 'Bypass', '-File', $MyInvocation.MyCommand.Path)
    foreach ($kv in @(@('Name', $Name), @('Listen', $Listen), @('Token', $Token))) {
        if (-not [string]::IsNullOrWhiteSpace($kv[1])) {
            $argList += "-$($kv[0])"; $argList += $kv[1]
        }
    }
    if ($KeepPolicy) { $argList += '-KeepPolicy' }
    Start-Process -FilePath 'powershell.exe' -ArgumentList $argList -Verb RunAs -Wait
    exit $LASTEXITCODE
}

# --- sshd presence check (informational; deploy-wre skips config rewrite if absent) ---
$sshdCap = Get-WindowsCapability -Online -Name 'OpenSSH.Server*' 2>$null |
    Where-Object { $_.State -eq 'Installed' }
if (-not $sshdCap) {
    Write-Host "[install-wre] OpenSSH Server NOT installed. Install + start it now? (y/n)"
    $answer = Read-Host
    if ($answer -eq 'y') {
        Add-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0' | Out-Null
        Start-Service sshd
        Set-Service -Name sshd -StartupType Automatic
        Write-Host "[install-wre] sshd installed and started."
    } else {
        Write-Warning "[install-wre] continuing without sshd — the bridge will not be reachable over SSH until you install/start it."
    }
} else {
    Write-Host "[install-wre] OpenSSH Server detected."
}

# --- run the real installer with the package's own python ---
Write-Host "[install-wre] running deploy-wre.py ..."
$pyArgs = @('-I', '-X', 'utf8', $deployScript, '--target-name', $Name,
            '--expected-listen', $Listen)
if ($KeepPolicy) { $pyArgs += '--keep-existing-policy' } else { $pyArgs += '--access-token'; $pyArgs += $Token }

& $pythonExe @pyArgs
$rc = $LASTEXITCODE

# --- post-install smoke ---
if ($rc -eq 0) {
    Write-Host ""
    Write-Host "== WRE v6 installed at C:\WRE\wre =="
    Write-Host "Controller side: add windows-remote-executor/targets/$Name.env"
    Write-Host "  (SSH host/user/key/port + TARGET_ACCESS_TOKEN = the same secret)"
    Write-Host "then: PYTHONPATH=. python3 -m v6.scripts.verify_v6_remote $Name"
}
exit $rc
