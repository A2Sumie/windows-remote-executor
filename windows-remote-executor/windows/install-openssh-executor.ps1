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
            $userProfile = "C:\Users\$TargetUser"
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
        $userProfile = "C:\Users\$UserName"
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

function Ensure-StartupConsole {
    param(
        [string]$Root,
        [string]$ListenAddress,
        [string]$UserName
    )

    $toolsScriptPath = Join-Path (Join-Path $Root 'tools') 'codex-startup-console.cmd'
    $startupDir = Join-Path (Resolve-UserProfilePath -UserName $UserName) 'AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup'
    $launcherPath = Join-Path $startupDir 'CodexRemote Console.cmd'

    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $toolsScriptPath) | Out-Null
    New-Item -ItemType Directory -Force -Path $startupDir | Out-Null

    $scriptContent = @(
        '@echo off',
        'title CodexRemote Console',
        'echo [%DATE% %TIME%] CodexRemote startup console',
        'echo Host: %COMPUTERNAME%',
        "for /f ""delims="" %%I in ('whoami') do echo User: %%I",
        "echo Listen: $ListenAddress`:22",
        "echo Root: $Root",
        'echo.',
        'for %%S in (Tailscale sshd) do (',
        '  sc.exe query %%S >nul 2>nul',
        '  if not errorlevel 1 (',
        '    sc.exe query %%S | find "RUNNING" >nul',
        '    if errorlevel 1 (',
        '      echo Starting %%S...',
        '      sc.exe start %%S >nul',
        '      ping -n 4 127.0.0.1 >nul',
        '    )',
        '  )',
        ')',
        'echo.',
        'echo Service status:',
        'for %%S in (Tailscale sshd) do (',
        '  sc.exe query %%S >nul 2>nul',
        '  if not errorlevel 1 (',
        '    echo ---',
        '    sc.exe query %%S',
        '  )',
        ')',
        'echo.',
        'echo Listener check:',
        "netstat -ano | findstr /R /C:""$ListenAddress`:22 .*LISTENING""",
        'echo.',
        'echo This window stays open for local recovery commands.',
        'prompt CodexRemote $P$G',
        ''
    ) -join "`r`n"

    $launcherContent = @(
        '@echo off',
        "start ""CodexRemote Console"" /max cmd.exe /k ""$toolsScriptPath""",
        ''
    ) -join "`r`n"

    Set-Content -LiteralPath $toolsScriptPath -Value $scriptContent -Encoding ascii
    Set-Content -LiteralPath $launcherPath -Value $launcherContent -Encoding ascii

    return [ordered]@{
        script_path = $toolsScriptPath
        launcher_path = $launcherPath
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
Write-Step 'Ensuring OpenSSH Server'
Ensure-OpenSSHServer
Write-Step 'Applying SSH listen address and firewall scope'
Set-SshListenAddress -Address $ListenAddress
Ensure-ScopedFirewallRule -Address $ListenAddress
Write-Step 'Preparing CodexRemote directories'
Ensure-CodexLayout -Root $CodexRoot
Write-Step 'Installing startup console'
$startupConsole = Ensure-StartupConsole -Root $CodexRoot -ListenAddress $ListenAddress -UserName $TargetUser
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

Write-Step 'Restarting sshd'
Restart-Service sshd

$summary = [ordered]@{
    sshd = (Get-Service sshd | Select-Object Name, Status, StartType)
    codex_root = $CodexRoot
    target_user = $TargetUser
    listen_address = $ListenAddress
    tailscale = (Get-Service Tailscale -ErrorAction SilentlyContinue | Select-Object Name, Status, StartType)
    default_shell = (Get-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell -ErrorAction SilentlyContinue).DefaultShell
    startup_console = $startupConsole
}

$summary | ConvertTo-Json -Depth 4
