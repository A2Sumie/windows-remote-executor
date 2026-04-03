[CmdletBinding()]
param(
    [string]$PublicKeyPath,
    [string]$AuthorizedKey,
    [string]$TargetUser = $env:USERNAME,
    [string]$CodexRoot = 'C:\CodexRemote',
    [string]$ListenAddress,
    [switch]$SetPowerShellDefaultShell,
    [switch]$InstallTailscale
)

$ErrorActionPreference = 'Stop'

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

function Get-LeafUserName {
    param([string]$UserName)

    if (-not $UserName) {
        return $env:USERNAME
    }

    $parts = $UserName -split '[\\/]'
    return ($parts | Where-Object { $_ } | Select-Object -Last 1)
}

function Resolve-CanonicalUserName {
    param([string]$UserName)

    if (-not $UserName) {
        return ('{0}\{1}' -f $env:COMPUTERNAME, $env:USERNAME)
    }

    $candidates = @()
    if ($UserName -match '[\\@]') {
        $candidates += $UserName
    } else {
        $candidates += ('{0}\{1}' -f $env:COMPUTERNAME, $UserName)
        $candidates += $UserName
    }

    foreach ($candidate in $candidates) {
        try {
            $principal = New-Object Security.Principal.NTAccount($candidate)
            $sid = $principal.Translate([Security.Principal.SecurityIdentifier])
            return $sid.Translate([Security.Principal.NTAccount]).Value
        } catch {
        }
    }

    return $UserName
}

function Ensure-OpenSSHServer {
    $capability = Get-WindowsCapability -Online | Where-Object Name -like 'OpenSSH.Server*'
    if ($null -eq $capability) {
        throw 'OpenSSH.Server capability not found on this Windows image.'
    }

    if ($capability.State -ne 'Installed') {
        Add-WindowsCapability -Online -Name $capability.Name | Out-Null
    }

    Set-Service -Name sshd -StartupType Automatic
    Start-Service sshd

    if (-not (Get-NetFirewallRule -Name OpenSSH-Server-In-TCP -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule `
            -Name OpenSSH-Server-In-TCP `
            -DisplayName 'OpenSSH Server (sshd)' `
            -Enabled True `
            -Direction Inbound `
            -Protocol TCP `
            -Action Allow `
            -LocalPort 22 | Out-Null
    }
}

function Ensure-ServiceStartup {
    param(
        [string]$Name,
        [ValidateSet('auto', 'delayed-auto')]
        [string]$StartMode = 'auto'
    )

    $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if (-not $service) {
        return
    }

    if ($StartMode -eq 'delayed-auto') {
        & sc.exe config $Name start= delayed-auto | Out-Null
        New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Services\$Name" -Name DelayedAutostart -PropertyType DWord -Value 1 -Force | Out-Null
    } else {
        & sc.exe config $Name start= auto | Out-Null
        New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Services\$Name" -Name DelayedAutostart -PropertyType DWord -Value 0 -Force | Out-Null
    }
}

function Ensure-SshRecoveryActions {
    & sc.exe failure sshd reset= 86400 actions= restart/5000/restart/15000/restart/30000 | Out-Null
    & sc.exe failureflag sshd 1 | Out-Null
}

function Ensure-SshDependsOnTailscale {
    $tailscale = Get-Service -Name Tailscale -ErrorAction SilentlyContinue
    if (-not $tailscale) {
        return
    }

    $dependency = (Get-CimInstance Win32_Service -Filter "Name='sshd'").Dependencies
    $needsUpdate = $true
    if ($dependency) {
        $needsUpdate = -not ($dependency -contains 'Tailscale')
    }

    if ($needsUpdate) {
        $deps = @('Tcpip', 'Tailscale')
        & sc.exe config sshd depend= ($deps -join '/') | Out-Null
    }
}

function Set-SshListenAddress {
    param([string]$Address)

    if (-not $Address) {
        return
    }

    [void][System.Net.IPAddress]::Parse($Address)

    $configPath = 'C:\ProgramData\ssh\sshd_config'
    if (-not (Test-Path -LiteralPath $configPath)) {
        throw "sshd_config not found: $configPath"
    }

    $config = Get-Content -LiteralPath $configPath -Raw
    $config = [Regex]::Replace($config, '(?m)^\s*ListenAddress\s+.*(?:\r?\n)?', '')

    $matchBlock = [Regex]::Match($config, '(?m)^[ \t]*Match\s+')
    if ($matchBlock.Success) {
        $beforeMatch = $config.Substring(0, $matchBlock.Index).TrimEnd()
        $fromMatch = $config.Substring($matchBlock.Index).TrimStart()
        $config = $beforeMatch + "`r`nListenAddress $Address`r`n`r`n" + $fromMatch
    } else {
        $config = $config.TrimEnd() + "`r`nListenAddress $Address`r`n"
    }

    Set-Content -LiteralPath $configPath -Value $config -Encoding ascii
}

function Ensure-ScopedFirewallRule {
    param([string]$Address)

    if (-not $Address) {
        return
    }

    $ruleName = 'Codex-OpenSSH-Tailscale-In-TCP'
    $existingRule = Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
    if ($existingRule) {
        Remove-NetFirewallRule -Name $ruleName | Out-Null
    }

    New-NetFirewallRule `
        -Name $ruleName `
        -DisplayName 'Codex OpenSSH Server (Tailscale)' `
        -Enabled True `
        -Direction Inbound `
        -Protocol TCP `
        -Action Allow `
        -LocalPort 22 `
        -LocalAddress $Address | Out-Null

    if (Get-NetFirewallRule -Name OpenSSH-Server-In-TCP -ErrorAction SilentlyContinue) {
        Disable-NetFirewallRule -Name OpenSSH-Server-In-TCP | Out-Null
    }
}

function Read-AuthorizedKey {
    if ($AuthorizedKey) {
        return $AuthorizedKey.Trim()
    }

    if ($PublicKeyPath) {
        if (-not (Test-Path -LiteralPath $PublicKeyPath)) {
            throw "Public key file not found: $PublicKeyPath"
        }

        return (Get-Content -LiteralPath $PublicKeyPath -Raw).Trim()
    }

    return $null
}

function Ensure-KeyFile {
    param(
        [string]$KeyText,
        [string]$FilePath
    )

    $dir = Split-Path -Parent $FilePath
    New-Item -ItemType Directory -Force -Path $dir | Out-Null

    $existing = ''
    if (Test-Path -LiteralPath $FilePath) {
        $existing = Get-Content -LiteralPath $FilePath -Raw
    }

    if ($existing -notmatch [Regex]::Escape($KeyText)) {
        Add-Content -LiteralPath $FilePath -Value $KeyText
    }
}

function Set-KeyPermissions {
    param(
        [string]$FilePath,
        [string]$UserName
    )

    & icacls $FilePath /inheritance:r | Out-Null
    & icacls $FilePath /grant "${UserName}:F" | Out-Null
}

function Ensure-AuthorizedKeys {
    param([string]$KeyText)

    if (-not $KeyText) {
        return
    }

    try {
        $principal = New-Object Security.Principal.NTAccount($TargetUser)
        $sid = $principal.Translate([Security.Principal.SecurityIdentifier]).Value
        $userProfile = (Get-CimInstance Win32_UserProfile -ErrorAction SilentlyContinue | Where-Object SID -eq $sid).LocalPath |
            Select-Object -First 1
        if (-not $userProfile) {
            $userProfile = 'C:\Users\{0}' -f (Get-LeafUserName -UserName $TargetUser)
        }

        $userKeyPath = Join-Path $userProfile '.ssh\authorized_keys'
        Ensure-KeyFile -KeyText $KeyText -FilePath $userKeyPath
        Set-KeyPermissions -FilePath $userKeyPath -UserName $TargetUser

        $adminMembers = Get-LocalGroupMember -Group 'Administrators' -ErrorAction SilentlyContinue
        if ($sid -and ($adminMembers | Where-Object SID -eq $sid)) {
            $adminKeyPath = 'C:\ProgramData\ssh\administrators_authorized_keys'
            Ensure-KeyFile -KeyText $KeyText -FilePath $adminKeyPath
            & icacls $adminKeyPath /inheritance:r | Out-Null
            & icacls $adminKeyPath /grant 'Administrators:F' 'SYSTEM:F' | Out-Null
        }
    } catch {
        Write-Warning ("Could not verify profile or administrator group membership for {0}: {1}" -f $TargetUser, $_.Exception.Message)
    }
}

function Resolve-UserProfilePath {
    param([string]$UserName)

    $principal = New-Object Security.Principal.NTAccount($UserName)
    $sid = $principal.Translate([Security.Principal.SecurityIdentifier]).Value
    $userProfile = (Get-CimInstance Win32_UserProfile -ErrorAction SilentlyContinue | Where-Object SID -eq $sid).LocalPath |
        Select-Object -First 1
    if (-not $userProfile) {
        $userProfile = 'C:\Users\{0}' -f (Get-LeafUserName -UserName $UserName)
    }

    return $userProfile
}

function Ensure-CodexLayout {
    param([string]$Root)

    $dirs = @(
        $Root,
        (Join-Path $Root 'tools'),
        (Join-Path $Root 'inbox'),
        (Join-Path $Root 'staging'),
        (Join-Path $Root 'apps'),
        (Join-Path $Root 'logs')
    )

    foreach ($dir in $dirs) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
}

function Quote-TaskCommandArgument {
    param([string]$Value)

    if ($null -eq $Value) {
        return '""'
    }

    if ($Value -match '[\s"]') {
        return ('"{0}"' -f ($Value.Replace('"', '\"')))
    }

    return $Value
}

function New-TaskCommand {
    param(
        [string]$ExecutablePath,
        [string[]]$ArgumentList
    )

    $parts = @((Quote-TaskCommandArgument -Value $ExecutablePath))
    foreach ($arg in $ArgumentList) {
        $parts += (Quote-TaskCommandArgument -Value $arg)
    }

    return ($parts -join ' ')
}

function Get-SshRepairTaskCommand {
    param(
        [string]$Root,
        [string]$ListenAddress
    )

    $nativeExePath = Join-Path (Join-Path $Root 'tools') 'WindowsRemoteExecutor.Native.exe'
    $repairLogPath = Join-Path (Join-Path $Root 'logs') 'sshd-repair.log'
    return (New-TaskCommand -ExecutablePath $nativeExePath -ArgumentList @(
            'repair-sshd',
            '--expected-listen-address', $ListenAddress,
            '--codex-root', $Root,
            '--log-path', $repairLogPath
        ))
}

function Ensure-SessionRepairTask {
    param(
        [string]$TaskName,
        [string]$UserName,
        [string]$TaskCommand
    )

    & schtasks.exe /Delete /TN $TaskName /F *> $null
    & schtasks.exe /Create /TN $TaskName /SC ONLOGON /RU $UserName /RL HIGHEST /TR $TaskCommand /F | Out-Null
    & schtasks.exe /Run /TN $TaskName *> $null
}

function Ensure-SshRepairTasks {
    param([string]$TaskCommand)

    & schtasks.exe /Delete /TN 'CodexRemote Sshd Repair Startup' /F *> $null
    & schtasks.exe /Delete /TN 'CodexRemote Sshd Repair Watch' /F *> $null
    & schtasks.exe /Create /TN 'CodexRemote Sshd Repair Startup' /SC ONSTART /RU SYSTEM /TR $TaskCommand /F | Out-Null
    & schtasks.exe /Create /TN 'CodexRemote Sshd Repair Watch' /SC MINUTE /MO 5 /RU SYSTEM /TR $TaskCommand /F | Out-Null
    & schtasks.exe /Run /TN 'CodexRemote Sshd Repair Watch' *> $null
}

function Remove-LegacyStartupConsoleArtifacts {
    param(
        [string]$Root,
        [string]$UserName
    )

    $startupDir = Join-Path (Resolve-UserProfilePath -UserName $UserName) 'AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup'
    $legacyStartupPath = Join-Path $startupDir 'CodexRemote Console.cmd'

    foreach ($path in @(
            (Join-Path (Join-Path $Root 'tools') 'codex-startup-console.cmd'),
            (Join-Path (Join-Path $Root 'tools') 'CodexRemote Console.cmd'),
            (Join-Path (Join-Path $Root 'tools') 'codex-repair-sshd.cmd'),
            $legacyStartupPath
        )) {
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
        }
    }

    & schtasks.exe /Delete /TN 'CodexRemote Console' /F *> $null
}

function Ensure-StartupRepair {
    param(
        [string]$Root,
        [string]$ListenAddress,
        [string]$UserName
    )

    $taskName = 'CodexRemote Sshd Repair Logon'
    $nativeExePath = Join-Path (Join-Path $Root 'tools') 'WindowsRemoteExecutor.Native.exe'
    $repairLogPath = Join-Path (Join-Path $Root 'logs') 'sshd-repair.log'
    $taskCommand = Get-SshRepairTaskCommand -Root $Root -ListenAddress $ListenAddress

    Remove-LegacyStartupConsoleArtifacts -Root $Root -UserName $UserName
    Ensure-SessionRepairTask -TaskName $taskName -UserName $UserName -TaskCommand $taskCommand
    Ensure-SshRepairTasks -TaskCommand $taskCommand

    return [ordered]@{
        native_tool_path = $nativeExePath
        repair_log_path = $repairLogPath
        logon_repair_task_name = $taskName
        startup_repair_task_name = 'CodexRemote Sshd Repair Startup'
        watch_repair_task_name = 'CodexRemote Sshd Repair Watch'
    }
}

function Set-DefaultShell {
    $shellPath = 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
    New-Item -Path 'HKLM:\SOFTWARE\OpenSSH' -Force | Out-Null
    Set-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell -Value $shellPath
}

function Ensure-Tailscale {
    $tailscale = Get-Command tailscale -ErrorAction SilentlyContinue
    if ($tailscale) {
        return
    }

    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        Write-Warning 'winget not found; skipping Tailscale installation.'
        return
    }

    & winget install --id Tailscale.Tailscale --accept-package-agreements --accept-source-agreements --silent
}

Assert-Admin
$TargetUser = Resolve-CanonicalUserName -UserName $TargetUser
Write-Step 'Ensuring OpenSSH Server'
Ensure-OpenSSHServer
Write-Step 'Applying SSH listen address and firewall scope'
Set-SshListenAddress -Address $ListenAddress
Ensure-ScopedFirewallRule -Address $ListenAddress
Write-Step 'Preparing CodexRemote directories'
Ensure-CodexLayout -Root $CodexRoot
Write-Step 'Installing headless sshd self-heal tasks'
$startupRepair = Ensure-StartupRepair -Root $CodexRoot -ListenAddress $ListenAddress -UserName $TargetUser
Write-Step 'Installing authorized keys'
Ensure-AuthorizedKeys -KeyText (Read-AuthorizedKey)

if ($SetPowerShellDefaultShell) {
    Write-Step 'Setting PowerShell as the default OpenSSH shell'
    Set-DefaultShell
}

if ($InstallTailscale) {
    Write-Step 'Ensuring Tailscale is installed'
    Ensure-Tailscale
}

Write-Step 'Configuring service startup order'
Ensure-ServiceStartup -Name Tailscale -StartMode auto
Ensure-ServiceStartup -Name sshd -StartMode auto
Ensure-SshDependsOnTailscale
Ensure-SshRecoveryActions

Write-Step 'Restarting sshd'
Restart-Service sshd

$summary = [ordered]@{
    sshd = (Get-Service sshd | Select-Object Name, Status, StartType)
    codex_root = $CodexRoot
    target_user = $TargetUser
    listen_address = $ListenAddress
    tailscale = (Get-Service Tailscale -ErrorAction SilentlyContinue | Select-Object Name, Status, StartType)
    default_shell = (Get-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell -ErrorAction SilentlyContinue).DefaultShell
    startup_repair = $startupRepair
}

$summary | ConvertTo-Json -Depth 4
