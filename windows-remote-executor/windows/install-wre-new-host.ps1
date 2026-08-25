[CmdletBinding()]
param(
    [string]$TargetName = $env:COMPUTERNAME,
    [string]$TargetUser = $env:USERNAME,
    [string]$ListenAddress,
    [string]$CodexRoot = 'C:\CodexRemote',
    [string]$AuthorizedKey,
    [string]$PublicKeyPath,
    [string]$AccessToken,
    [ValidateSet('private-only', 'public-with-token')]
    [string]$ExposureMode = 'private-only',
    [ValidateSet('standard', 'argv-only')]
    [string]$CommandMode = 'standard',
    [string]$PolicyLabel,
    [switch]$InstallTailscale,
    [switch]$NoRunGuard
)

$ErrorActionPreference = 'Stop'
$ScriptRoot = Split-Path -Parent $PSCommandPath

function Write-Step {
    param([string]$Message)
    Write-Host ("==> " + $Message) -ForegroundColor Cyan
}

function Assert-Admin {
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentIdentity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) {
        throw 'Run this script from an elevated PowerShell session.'
    }
}

function Get-IPv4Priority {
    param([string]$Address)

    $octets = $Address.Split('.')
    if ($octets.Count -ne 4) {
        return 100
    }

    $first = [int]$octets[0]
    $second = [int]$octets[1]
    if ($first -eq 100 -and $second -ge 64 -and $second -le 127) { return 0 }
    if ($first -eq 10) { return 1 }
    if ($first -eq 192 -and $second -eq 168) { return 2 }
    if ($first -eq 127) { return 3 }
    if ($first -eq 169 -and $second -eq 254) { return 4 }
    return 100
}

function Resolve-ListenAddress {
    if ($ListenAddress) {
        [void][System.Net.IPAddress]::Parse($ListenAddress)
        return $ListenAddress
    }

    $candidate = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $_.IPAddress -and
            (Get-IPv4Priority -Address $_.IPAddress) -lt 100
        } |
        Sort-Object @{ Expression = { Get-IPv4Priority -Address $_.IPAddress } }, InterfaceMetric |
        Select-Object -ExpandProperty IPAddress -First 1

    if (-not $candidate) {
        throw 'No suitable private IPv4 address detected. Pass -ListenAddress explicitly.'
    }

    return $candidate
}

function Read-AuthorizedKey {
    if ($AuthorizedKey) {
        return $AuthorizedKey.Trim()
    }

    if (-not $PublicKeyPath) {
        $packagedKey = Join-Path $ScriptRoot 'authorized_key.pub'
        if (Test-Path -LiteralPath $packagedKey) {
            $PublicKeyPath = $packagedKey
        }
    }

    if ($PublicKeyPath) {
        if (-not (Test-Path -LiteralPath $PublicKeyPath)) {
            throw "Public key file not found: $PublicKeyPath"
        }
        return (Get-Content -LiteralPath $PublicKeyPath -Raw).Trim()
    }

    throw 'Provide -AuthorizedKey or -PublicKeyPath, or place authorized_key.pub beside this script.'
}

function Read-AccessToken {
    if ($AccessToken) {
        return $AccessToken.Trim()
    }

    $packagedToken = Join-Path $ScriptRoot 'access-token.txt'
    if (Test-Path -LiteralPath $packagedToken) {
        return (Get-Content -LiteralPath $packagedToken -Raw).Trim()
    }

    return $null
}

function Get-Sha256Hex {
    param([string]$Text)

    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes($Text)
        $hash = $sha256.ComputeHash($bytes)
        return (($hash | ForEach-Object { $_.ToString('x2') }) -join '')
    } finally {
        $sha256.Dispose()
    }
}

function Set-Utf8NoBom {
    param(
        [string]$Path,
        [string]$Value
    )

    $encoding = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($Path, $Value, $encoding)
}

function Ensure-Launcher {
    param(
        [string]$LauncherPath,
        [string]$CurrentReleaseFile,
        [string]$FallbackNativeExe
    )

    $launcher = @"
@echo off
setlocal
set "WINDOWS_REMOTE_EXECUTOR_CURRENT_FILE=$CurrentReleaseFile"
if exist "%WINDOWS_REMOTE_EXECUTOR_CURRENT_FILE%" (
  set /p WINDOWS_REMOTE_EXECUTOR_CURRENT=<"%WINDOWS_REMOTE_EXECUTOR_CURRENT_FILE%"
  if exist "%WINDOWS_REMOTE_EXECUTOR_CURRENT%\WindowsRemoteExecutor.Native.exe" (
    "%WINDOWS_REMOTE_EXECUTOR_CURRENT%\WindowsRemoteExecutor.Native.exe" %*
    exit /b %ERRORLEVEL%
  )
)
set "WINDOWS_REMOTE_EXECUTOR_FALLBACK=$FallbackNativeExe"
if exist "%WINDOWS_REMOTE_EXECUTOR_FALLBACK%" (
  "%WINDOWS_REMOTE_EXECUTOR_FALLBACK%" %*
  exit /b %ERRORLEVEL%
)
echo error: WindowsRemoteExecutor native payload not found. 1>&2
exit /b 127
"@
    Set-Content -LiteralPath $LauncherPath -Value $launcher -Encoding ascii
}

function Install-NativePayload {
    param([string]$Root)

    $nativeSource = Join-Path $ScriptRoot 'native'
    $sourceExe = Join-Path $nativeSource 'WindowsRemoteExecutor.Native.exe'
    if (-not (Test-Path -LiteralPath $sourceExe)) {
        throw "Native payload not found: $sourceExe"
    }

    $toolsDir = Join-Path $Root 'tools'
    $releasesDir = Join-Path $toolsDir 'releases'
    $releaseDir = Join-Path $releasesDir ('bootstrap-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    New-Item -ItemType Directory -Force -Path $toolsDir, $releasesDir, $releaseDir | Out-Null

    Copy-Item -Path (Join-Path $nativeSource '*') -Destination $toolsDir -Recurse -Force
    Copy-Item -Path (Join-Path $nativeSource '*') -Destination $releaseDir -Recurse -Force

    $currentReleaseFile = Join-Path $toolsDir 'current-release.txt'
    Set-Content -LiteralPath $currentReleaseFile -Value $releaseDir -Encoding ascii

    $launcherPath = Join-Path $toolsDir 'WindowsRemoteExecutor.cmd'
    $fallbackNativeExe = Join-Path $toolsDir 'WindowsRemoteExecutor.Native.exe'
    Ensure-Launcher -LauncherPath $launcherPath -CurrentReleaseFile $currentReleaseFile -FallbackNativeExe $fallbackNativeExe

    return [ordered]@{
        tools_dir = $toolsDir
        release_dir = $releaseDir
        native_exe = $fallbackNativeExe
        launcher = $launcherPath
        current_release_file = $currentReleaseFile
    }
}

function Write-Policy {
    param(
        [System.Collections.IDictionary]$NativeInstall,
        [string]$ExpectedListenAddress,
        [string]$PlainToken
    )

    if ($ExposureMode -eq 'public-with-token' -and -not $PlainToken) {
        throw 'public-with-token mode requires -AccessToken or packaged access-token.txt.'
    }

    $label = $PolicyLabel
    if (-not $label) {
        if ($PlainToken) {
            if ($ExposureMode -eq 'public-with-token') {
                $label = 'PUBLIC-WITH-TOKEN EXPLICIT'
            } else {
                $label = 'PRIVATE-ONLY TOKEN-REQUIRED'
            }
        } else {
            $label = 'PRIVATE-ONLY'
        }
    }

    $policy = [ordered]@{
        expectedListenAddress = $ExpectedListenAddress
        exposureMode = $ExposureMode
        commandMode = $CommandMode
        label = $label
        accessTokenSha256 = $null
        updatedAt = [DateTimeOffset]::Now.ToString('o')
    }
    if ($PlainToken) {
        $policy.accessTokenSha256 = Get-Sha256Hex -Text $PlainToken
    }

    $policyJson = $policy | ConvertTo-Json -Depth 4
    $policyPath = Join-Path $NativeInstall['tools_dir'] 'access-policy.json'
    Set-Utf8NoBom -Path $policyPath -Value $policyJson
    Set-Utf8NoBom -Path (Join-Path $NativeInstall['release_dir'] 'access-policy.json') -Value $policyJson
    return $policyPath
}

function Install-GuardTasks {
    param(
        [hashtable]$NativeInstall,
        [string]$ExpectedListenAddress
    )

    $guardLogPath = Join-Path (Join-Path $CodexRoot 'logs') 'sshd-guard.log'
    $guardScriptPath = Join-Path $NativeInstall['tools_dir'] 'codex-sshd-guard.cmd'
    $launcherPath = $NativeInstall['launcher']
    $guardScript = @"
@echo off
setlocal EnableExtensions
call "$launcherPath" guard-sshd --expected-listen-address $ExpectedListenAddress --log-path "$guardLogPath"
exit /b %ERRORLEVEL%
"@
    Set-Content -LiteralPath $guardScriptPath -Value $guardScript -Encoding ascii

    $taskCommand = '"{0}"' -f $guardScriptPath
    cmd.exe /c 'schtasks.exe /Delete /TN "CodexRemote Sshd Guard Startup" /F >NUL 2>NUL & exit /b 0'
    cmd.exe /c 'schtasks.exe /Delete /TN "CodexRemote Sshd Guard Watch" /F >NUL 2>NUL & exit /b 0'
    & schtasks.exe /Create /TN 'CodexRemote Sshd Guard Startup' /SC ONSTART /RU SYSTEM /TR $taskCommand /F | Out-Null
    & schtasks.exe /Create /TN 'CodexRemote Sshd Guard Watch' /SC MINUTE /MO 5 /RU SYSTEM /TR $taskCommand /F | Out-Null

    if (-not $NoRunGuard) {
        & $NativeInstall['launcher'] guard-sshd --expected-listen-address $ExpectedListenAddress --log-path $guardLogPath | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw "guard-sshd failed with exit code $LASTEXITCODE"
        }
    }

    return [ordered]@{
        guard_script = $guardScriptPath
        guard_log = $guardLogPath
        startup_task_name = 'CodexRemote Sshd Guard Startup'
        watch_task_name = 'CodexRemote Sshd Guard Watch'
    }
}

function ConvertTo-EnvValue {
    param([string]$Value)

    if ($Value -match '^[A-Za-z0-9_./:@+-]+$') {
        return $Value
    }

    return "'" + $Value.Replace("'", "'\''") + "'"
}

function Write-TargetTemplate {
    param(
        [string]$ExpectedListenAddress,
        [string]$PlainToken,
        [string]$PolicyPath
    )

    $safeName = ($TargetName -replace '[^A-Za-z0-9_.-]', '-')
    $templatePath = Join-Path $ScriptRoot ("target-{0}.env" -f $safeName)
    $logTemplatePath = Join-Path (Join-Path $CodexRoot 'logs') ("target-{0}.env" -f $safeName)
    $label = if ($PolicyLabel) { $PolicyLabel } elseif ($PlainToken) { 'PRIVATE-ONLY TOKEN-REQUIRED' } else { 'PRIVATE-ONLY' }

    $lines = @(
        "TARGET_NAME=$(ConvertTo-EnvValue -Value $TargetName)",
        "TARGET_HOST=$(ConvertTo-EnvValue -Value $ExpectedListenAddress)",
        "TARGET_USER=$(ConvertTo-EnvValue -Value $TargetUser)",
        'TARGET_PORT=22',
        '# Fill this on the macOS/Linux controller:',
        '# TARGET_KEY=/Users/you/.ssh/id_ed25519',
        "TARGET_EXPECTED_LISTEN_ADDRESS=$(ConvertTo-EnvValue -Value $ExpectedListenAddress)",
        "TARGET_EXPOSURE_MODE=$(ConvertTo-EnvValue -Value $ExposureMode)",
        "TARGET_COMMAND_MODE=$(ConvertTo-EnvValue -Value $CommandMode)",
        "TARGET_POLICY_LABEL=$(ConvertTo-EnvValue -Value $label)",
        "TARGET_NATIVE_EXE=$(ConvertTo-EnvValue -Value ((Join-Path (Join-Path $CodexRoot 'tools') 'WindowsRemoteExecutor.Native.exe') -replace '\\', '/'))",
        "TARGET_NATIVE_LAUNCHER=$(ConvertTo-EnvValue -Value ((Join-Path (Join-Path $CodexRoot 'tools') 'WindowsRemoteExecutor.cmd') -replace '\\', '/'))",
        "TARGET_NATIVE_CURRENT_FILE=$(ConvertTo-EnvValue -Value ((Join-Path (Join-Path $CodexRoot 'tools') 'current-release.txt') -replace '\\', '/'))",
        "TARGET_POLICY_PATH=$(ConvertTo-EnvValue -Value ($PolicyPath -replace '\\', '/'))"
    )

    if ($PlainToken) {
        $lines += "TARGET_ACCESS_TOKEN=$(ConvertTo-EnvValue -Value $PlainToken)"
    }

    Set-Utf8NoBom -Path $templatePath -Value ($lines -join "`n")
    Set-Utf8NoBom -Path $logTemplatePath -Value ($lines -join "`n")
    return $templatePath
}

Assert-Admin
$resolvedListenAddress = Resolve-ListenAddress
$authorizedKeyText = Read-AuthorizedKey
$plainAccessToken = Read-AccessToken

Write-Step 'Installing native executor payload'
$nativeInstall = Install-NativePayload -Root $CodexRoot

Write-Step 'Bootstrapping OpenSSH and repair tasks'
$bootstrapArgs = @(
    'bootstrap',
    '--authorized-key', $authorizedKeyText,
    '--user', $TargetUser,
    '--listen-address', $resolvedListenAddress,
    '--codex-root', $CodexRoot,
    '--clear-default-shell'
)
if ($InstallTailscale) {
    $bootstrapArgs += '--install-tailscale'
}
& $nativeInstall.native_exe @bootstrapArgs | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "native bootstrap failed with exit code $LASTEXITCODE"
}

Write-Step 'Writing access policy baseline'
$policyPath = Write-Policy -NativeInstall $nativeInstall -ExpectedListenAddress $resolvedListenAddress -PlainToken $plainAccessToken

Write-Step 'Installing sshd guard tasks'
$guard = Install-GuardTasks -NativeInstall $nativeInstall -ExpectedListenAddress $resolvedListenAddress

Write-Step 'Writing controller target template'
$targetTemplate = Write-TargetTemplate -ExpectedListenAddress $resolvedListenAddress -PlainToken $plainAccessToken -PolicyPath $policyPath

$summary = [ordered]@{
    ok = $true
    target_name = $TargetName
    target_user = $TargetUser
    listen_address = $resolvedListenAddress
    codex_root = $CodexRoot
    native = $nativeInstall
    policy_path = $policyPath
    guard = $guard
    target_template = $targetTemplate
    next_steps = @(
        'Copy the target-*.env content to windows-remote-executor/targets/<name>.env on the controller.',
        'Fill TARGET_KEY with the private key path that matches the installed public key.',
        'Run: ./windows-remote-executor/bin/win-remote probe <name>',
        'Run: ./windows-remote-executor/scripts/verify-v3-remote-cases.sh <name>'
    )
}

$summary | ConvertTo-Json -Depth 6
