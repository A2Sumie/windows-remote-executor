# install-wre.ps1 — one-command on-host installer for the WRE v6 bootstrap package.
#
# Wraps deploy-wre.py: self-elevates, locates the package next to itself,
# validates token args, and drives the embedded python. No prerequisites —
# the package carries its own CPython.
#
# Works from ANY location: any drive, any path, spaces/Unicode in the folder
# name are fine (the elevated relaunch passes a base64-encoded command block,
# immune to quoting damage). The ONLY requirement is that this script sits in
# the extracted zip root (deploy-wre.py + wre\python next to it) — which is
# how the bootstrap zip lays files out.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File "<anywhere>\install-wre.ps1" ^
#       -Name <host-alias> -Listen <ip-or-empty> -Token <random-long-secret>
#       [-Dest C:\somewhere\wre]     # default C:\WRE\wre
#   # re-install over an existing tree without retyping the token:
#   ... -KeepPolicy

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)] [string]$Name,
    [Parameter(Mandatory = $false)] [string]$Listen,
    [Parameter(Mandatory = $false)] [string]$Token,
    [Parameter(Mandatory = $false)] [string]$Dest,
    [Parameter(Mandatory = $false)] [switch]$KeepPolicy,
    [Parameter(Mandatory = $false)] [switch]$NoSelfElevate
)

$ErrorActionPreference = 'Stop'

# --- locate the package relative to THIS script file (any cwd, any path) ---
$scriptFile = if ($MyInvocation.MyCommand.Path) { $MyInvocation.MyCommand.Path } else { $PSCommandPath }
if (-not $scriptFile) {
    Write-Error "cannot resolve installer script path (run it from a .ps1 file, not pasted inline)"
    exit 2
}
$pkgDir  = Split-Path -Parent $scriptFile
$deployScript = Join-Path $pkgDir 'deploy-wre.py'
$pythonExe    = Join-Path $pkgDir 'wre\python\python.exe'
foreach ($f in @($deployScript, $pythonExe)) {
    if (-not (Test-Path -LiteralPath $f)) {
        Write-Error "package file missing: $f — install-wre.ps1 must sit in the extracted zip root"
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

# --- collect the launch parameters once; reused by BOTH the elevated relaunch
#     (base64-encoded, quoting-proof) and direct execution ---
function Get-ChildArgs([bool]$forEncoded) {
    # returns the deploy-wre.py argument array (no script/python paths)
    $a = @()
    if (-not [string]::IsNullOrWhiteSpace($Name))  { $a += '--target-name';   $a += $Name }
    if (-not [string]::IsNullOrWhiteSpace($Listen)){ $a += '--expected-listen'; $a += $Listen }
    if (-not [string]::IsNullOrWhiteSpace($Dest))  { $a += '--dest';         $a += $Dest }
    if ($KeepPolicy) { $a += '--keep-existing-policy' }
    else             { $a += '--access-token'; $a += $Token }
    return ,$a
}
$childArgs = Get-ChildArgs $false

# --- sshd presence check (informational; deploy-wre skips config rewrite when absent).
#     Only meaningful pre-elevation; skipped in the elevated run to avoid double prompts. ---
if (-not $NoSelfElevate) {
    try {
    $sshdCap = Get-WindowsCapability -Online -Name 'OpenSSH.Server*' 2>$null |
        Where-Object { $_.State -eq 'Installed' }
    } catch {
        $sshdCap = $true  # non-Windows platform: skip the sshd prompt entirely
    }
    if ($false -eq $sshdCap) {
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
}

# --- self-elevate via EncodedCommand: fully quoting-proof for spaces/Unicode
#     in the extract path and argument values ---
try {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
               ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
} catch {
    $isAdmin = $true  # non-Windows host / principal API unavailable: proceed (deploy-wre re-checks elevation itself)
}
if (-not $isAdmin -and -not $NoSelfElevate) {
    Write-Host "[install-wre] relaunching elevated..."
    # Rebuild the param string from typed values (NOT raw text), then encode.
    function esc([string]$v) { return "'" + ($v -replace "'", "''") + "'" }
    $inner = '& ' + (esc $scriptFile)
    foreach ($kv in @(@('-Name', $Name), @('-Listen', $Listen), @('-Token', $Token), @('-Dest', $Dest))) {
        if (-not [string]::IsNullOrWhiteSpace($kv[1])) { $inner += ' ' + $kv[0] + ' ' + (esc $kv[1]) }
    }
    if ($KeepPolicy) { $inner += ' -KeepPolicy' }
    $inner += ' -NoSelfElevate'   # elevation happens exactly once
    $bytes  = [Text.Encoding]::Unicode.GetBytes($inner)
    $encoded = [Convert]::ToBase64String($bytes)
    $proc = Start-Process -FilePath 'powershell.exe' `
        -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encoded) `
        -Verb RunAs -Wait -PassThru
    exit $proc.ExitCode
}

# --- run the real installer with the package's own python ---
Write-Host "[install-wre] running deploy-wre.py ..."
Write-Host "[install-wre] pkg: $pkgDir"
$pyArgs = @('-I', '-X', 'utf8', $deployScript) + $childArgs
& $pythonExe @pyArgs
$rc = $LASTEXITCODE

# --- post-install smoke ---
if ($rc -eq 0) {
    $destShown = if ([string]::IsNullOrWhiteSpace($Dest)) { 'C:\WRE\wre' } else { $Dest }
    Write-Host ""
    Write-Host "== WRE v6 installed at $destShown =="
    $nameShown = if ([string]::IsNullOrWhiteSpace($Name)) { '<NAME>' } else { $Name }
    Write-Host "Controller side: add windows-remote-executor/targets/$nameShown.env"
    Write-Host "  (SSH host/user/key/port + TARGET_ACCESS_TOKEN = the same secret)"
    Write-Host "then: PYTHONPATH=. python3 -m v6.scripts.verify_v6_remote $Name"
}
exit $rc
