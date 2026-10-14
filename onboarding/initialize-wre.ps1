#requires -Version 7.0
<#
.SYNOPSIS
Install the verified WRE v6.1.2 package using a controller-generated hash-only bootstrap.
.DESCRIPTION
Run locally in an Administrator pwsh 7 console. -CheckOnly performs no configuration writes.
The controller token stays on the controller; this script receives only its SHA256 and public key.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string]$ConfigFile,
    [Parameter(Mandatory)] [string]$PackageDirectory,
    [switch]$PreserveToken,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
function Merge-WreApplySpec {
    param([System.Collections.IDictionary]$Existing, [string]$ListenAddress)
    $merged = [ordered]@{}
    foreach ($key in $Existing.Keys) { $merged[$key] = $Existing[$key] }
    $merged.expectedListenAddress = $ListenAddress
    $merged.ensureRepairTasks = $true
    return $merged
}
$setupExit = 1
$setupTranscript = $false
$setupResult = [ordered]@{
    hostname = [Environment]::MachineName
    timestampUtc = [DateTime]::UtcNow.ToString('o')
    pwshVersion = $PSVersionTable.PSVersion.ToString()
    checkOnly = [bool]$CheckOnly
    success = $false
    phase = 'preflight'
}
try {
    if (-not $IsWindows -or $PSVersionTable.PSEdition -ne 'Core') { throw 'Windows and PowerShell 7 Core are required.' }
    $setupConfig = Get-Content -LiteralPath $ConfigFile -Raw | ConvertFrom-Json -AsHashtable
    if ($setupConfig.schemaVersion -ne 1) { throw 'Unsupported bootstrap schema.' }
    if ($setupConfig.name -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$') { throw 'Invalid target alias.' }
    $setupIp = [Net.IPAddress]::Parse([string]$setupConfig.host)
    $setupOctets = $setupIp.GetAddressBytes()
    if ($setupOctets.Length -ne 4 -or $setupOctets[0] -ne 100 -or $setupOctets[1] -lt 64 -or $setupOctets[1] -gt 127) {
        throw 'The bootstrap must contain this Windows host''s actual Tailscale IPv4.'
    }
    $setupListen = $setupIp.ToString()
    $setupController = [Net.IPAddress]::Parse([string]$setupConfig.controllerAddress)
    $setupControllerOctets = $setupController.GetAddressBytes()
    if ($setupControllerOctets.Length -ne 4 -or $setupControllerOctets[0] -ne 100 -or $setupControllerOctets[1] -lt 64 -or $setupControllerOctets[1] -gt 127) {
        throw 'Bootstrap controllerAddress must be the controller''s actual Tailscale IPv4.'
    }
    if (-not (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -eq $setupListen })) {
        throw 'Bootstrap address is not assigned to this host; regenerate it for the correct Tailscale peer.'
    }
    if ($setupConfig.tokenSha256 -notmatch '^[0-9a-f]{64}$') { throw 'Bootstrap token SHA256 is invalid.' }
    $setupKey = ([string]$setupConfig.publicKey).Trim()
    if ($setupKey -notmatch '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521))\s+[A-Za-z0-9+/]+=*(\s+[^\r\n]*)?$') {
        throw 'Bootstrap must contain one OpenSSH public key.'
    }
    if (-not $setupConfig.user -or $setupConfig.user -match '[,\r\n]') { throw 'Invalid local SSH account.' }
    $setupUser = Get-LocalUser -Name ([string]$setupConfig.user)
    if (-not $setupUser.Enabled) { throw 'The selected local SSH account is disabled.' }
    $setupProfile = Get-CimInstance Win32_UserProfile -Filter ("SID='" + $setupUser.SID.Value + "'")
    if (-not $setupProfile.LocalPath) { throw 'The SSH account has no existing Windows profile; sign in once first.' }
    $setupPackage = (Resolve-Path -LiteralPath $PackageDirectory).Path
    $setupSource = Join-Path $setupPackage 'wre'
    $setupPython = Join-Path $setupSource 'python/python.exe'
    $setupDeploy = Join-Path $setupPackage 'deploy-wre.py'
    foreach ($setupRequired in @($setupPython, $setupDeploy, (Join-Path $setupSource 'rpc.py'))) {
        if (-not (Test-Path -LiteralPath $setupRequired -PathType Leaf)) { throw "Verified package file missing: $setupRequired" }
    }
    if (Test-Path -LiteralPath (Join-Path $setupSource 'access-policy.json')) {
        throw 'Package contains a host access policy; use the clean verified release ZIP.'
    }
    $setupDest = 'C:\WRE\wre'
    if ([IO.Path]::GetFullPath($setupSource).TrimEnd('\') -eq [IO.Path]::GetFullPath($setupDest).TrimEnd('\')) {
        throw 'Extract the release to a separate directory; the live tree is not an install package.'
    }
    $setupSelftestText = & $setupPython -I -X utf8 (Join-Path $setupSource 'rpc.py') selftest
    if ($LASTEXITCODE -ne 0) { throw 'Package selftest failed before any host configuration changes.' }
    $setupSelftest = ($setupSelftestText | Select-Object -Last 1) | ConvertFrom-Json
    if ($setupSelftest.protocolVersion -ne 6 -or -not $setupSelftest.data.actions) {
        throw 'Package selftest did not report the WRE v6 action surface.'
    }
    $setupResult.packageSelftest = $true
    $setupPolicyPath = Join-Path $setupDest 'access-policy.json'
    $setupPolicy = if (Test-Path -LiteralPath $setupPolicyPath) {
        Get-Content -LiteralPath $setupPolicyPath -Raw | ConvertFrom-Json -AsHashtable
    } else { [ordered]@{} }
    $setupApplyPath = Join-Path $setupDest 'apply-tasks.json'
    $setupPreviousApply = if (Test-Path -LiteralPath $setupApplyPath) {
        Get-Content -LiteralPath $setupApplyPath -Raw | ConvertFrom-Json -AsHashtable
    } else { $null }
    if ($PreserveToken -and $setupPolicy.accessTokenSha256 -ne $setupConfig.tokenSha256) {
        throw 'Existing host token does not match this bootstrap; use --reuse on the controller or explicitly rotate the lost token.'
    }
    foreach ($setupCommand in @('Get-NetIPAddress', 'New-NetFirewallRule', 'Set-NetFirewallRule', 'Get-ScheduledTask', 'Get-LocalUser')) {
        Get-Command $setupCommand -ErrorAction Stop | Out-Null
    }
    $setupResult.target = [string]$setupConfig.name
    $setupResult.listenAddress = $setupListen
    $setupResult.user = $setupUser.Name
    $setupResult.tokenMatchesExisting = $setupPolicy.accessTokenSha256 -eq $setupConfig.tokenSha256
    $setupResult.tokenPreserved = [bool]$PreserveToken
    $setupResult.sshInstalled = [bool](Get-Service sshd -ErrorAction SilentlyContinue)
    if ($CheckOnly) {
        $setupResult.success = $true
        $setupResult.phase = 'preflight-complete'
        $setupExit = 0
    } else {
        $setupPrincipal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
        if (-not $setupPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
            throw 'Open pwsh 7 as Administrator and rerun locally; the initializer does not hide errors in an elevated child window.'
        }
        $setupStamp = Get-Date -Format 'yyyyMMdd-HHmmss-fffffff'
        $setupLogRoot = 'C:\WRE\logs'
        New-Item -ItemType Directory -Path $setupLogRoot -Force | Out-Null
        $setupResult.transcript = Join-Path $setupLogRoot ('onboarding-' + $setupStamp + '.log')
        Start-Transcript -LiteralPath $setupResult.transcript | Out-Null
        $setupTranscript = $true
        $setupResult.phase = 'openssh'
        if (-not (Get-Service sshd -ErrorAction SilentlyContinue)) {
            Add-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0' | Out-Null
        }
        $setupSshd = Join-Path $env:SystemRoot 'System32/OpenSSH/sshd.exe'
        if (-not (Test-Path -LiteralPath $setupSshd -PathType Leaf)) { throw 'Windows OpenSSH sshd.exe was not found after installation.' }
        $setupResult.phase = 'firewall'
        $setupFirewall = @{
            Enabled = 'True'; Direction = 'Inbound'; Action = 'Allow'; Protocol = 'TCP'
            LocalPort = 22; LocalAddress = $setupListen; RemoteAddress = '100.64.0.0/10'
            Program = $setupSshd; Profile = 'Any'
        }
        if (Get-NetFirewallRule -Name 'WRE-SSH-Tailnet' -ErrorAction SilentlyContinue) {
            Set-NetFirewallRule -Name 'WRE-SSH-Tailnet' @setupFirewall
        } else {
            New-NetFirewallRule -Name 'WRE-SSH-Tailnet' -DisplayName 'WRE-SSHD-Inbound-22' @setupFirewall | Out-Null
        }
        if (Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue) {
            Set-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' @setupFirewall
        }
        Set-Service -Name sshd -StartupType Automatic
        if (-not (Test-Path -LiteralPath (Join-Path $env:ProgramData 'ssh/sshd_config'))) {
            Start-Service -Name sshd
        }
        $setupResult.phase = 'public-key'
        $setupContext = 'user=' + $setupUser.Name.ToLowerInvariant() + ',host=' + $env:COMPUTERNAME + ',addr=' + $setupController.ToString() + ',laddr=' + $setupListen + ',lport=22'
        $setupEffective = & $setupSshd -T -C $setupContext
        if ($LASTEXITCODE -ne 0) { throw 'Cannot read effective sshd configuration.' }
        $setupDirective = $setupEffective | Where-Object { $_ -match '^authorizedkeysfile\s' } | Select-Object -First 1
        if (-not $setupDirective) { throw 'No effective AuthorizedKeysFile was returned.' }
        $setupKeyFiles = @()
        foreach ($setupRawPath in (($setupDirective -split '\s+') | Select-Object -Skip 1)) {
            $setupKeyPath = $setupRawPath.Replace('__PROGRAMDATA__', $env:ProgramData).Replace('%h', $setupProfile.LocalPath)
            if (-not [IO.Path]::IsPathRooted($setupKeyPath)) { $setupKeyPath = Join-Path $setupProfile.LocalPath $setupKeyPath }
            if ([IO.Path]::GetFileName($setupKeyPath) -notin @('authorized_keys', 'administrators_authorized_keys')) {
                throw 'Unexpected AuthorizedKeysFile layout; configure its key file explicitly.'
            }
            New-Item -ItemType Directory -Path (Split-Path -Parent $setupKeyPath) -Force | Out-Null
            $setupLines = @()
            if (Test-Path -LiteralPath $setupKeyPath) {
                Copy-Item -LiteralPath $setupKeyPath -Destination ($setupKeyPath + '.before-wre-' + $setupStamp)
                $setupLines = @(Get-Content -LiteralPath $setupKeyPath)
            }
            $setupBlob = ($setupKey -split '\s+')[1]
            if (-not ($setupLines | Where-Object { ($_ -split '\s+') -contains $setupBlob })) {
                [IO.File]::WriteAllText($setupKeyPath, (($setupLines + $setupKey) -join [Environment]::NewLine) + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
            }
            $setupAcl = [Security.AccessControl.FileSecurity]::new()
            $setupAcl.SetAccessRuleProtection($true, $false)
            $setupAdminSid = [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
            $setupSystemSid = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
            $setupAcl.SetOwner($setupAdminSid)
            $setupAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($setupAdminSid, 'FullControl', 'Allow'))
            $setupAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($setupSystemSid, 'FullControl', 'Allow'))
            if ([IO.Path]::GetFileName($setupKeyPath) -eq 'authorized_keys') {
                $setupAcl.SetOwner($setupUser.SID)
                $setupAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($setupUser.SID, 'FullControl', 'Allow'))
            }
            Set-Acl -LiteralPath $setupKeyPath -AclObject $setupAcl
            $setupKeyFiles += $setupKeyPath
        }
        $setupResult.keyFiles = $setupKeyFiles
        $setupResult.phase = 'policy'
        New-Item -ItemType Directory -Path $setupDest -Force | Out-Null
        if (Test-Path -LiteralPath $setupPolicyPath) {
            $setupResult.policyBackup = $setupPolicyPath + '.before-onboarding-' + $setupStamp
            Copy-Item -LiteralPath $setupPolicyPath -Destination $setupResult.policyBackup
        }
        $setupPolicy.expectedListenAddress = $setupListen
        $setupPolicy.accessTokenSha256 = $setupConfig.tokenSha256
        $setupPolicy.exposureMode = 'private-only'
        $setupPolicy.commandMode = 'v6'
        $setupPolicy.label = 'PRIVATE-ONLY'
        $setupPolicy.updatedAt = [DateTime]::UtcNow.ToString('o')
        $setupPolicyTemporary = $setupPolicyPath + '.onboarding-' + $setupStamp + '.tmp'
        [IO.File]::WriteAllText($setupPolicyTemporary, ($setupPolicy | ConvertTo-Json -Depth 20), [Text.UTF8Encoding]::new($false))
        [IO.File]::Move($setupPolicyTemporary, $setupPolicyPath, $true)
        $setupResult.phase = 'native-install'
        if ($null -ne $setupPreviousApply) {
            $setupResult.applySpecBackup = $setupApplyPath + '.before-onboarding-' + $setupStamp
            Copy-Item -LiteralPath $setupApplyPath -Destination $setupResult.applySpecBackup
        }
        & $setupPython -I -X utf8 $setupDeploy --target-name ([string]$setupConfig.name) --expected-listen $setupListen --keep-existing-policy --no-restart-sshd
        if ($LASTEXITCODE -ne 0) { throw "Native installer failed with exit code $LASTEXITCODE; see the transcript." }
        if ($null -ne $setupPreviousApply) {
            $setupMergedApply = Merge-WreApplySpec -Existing $setupPreviousApply -ListenAddress $setupListen
            $setupApplyTemporary = $setupApplyPath + '.onboarding-' + $setupStamp + '.tmp'
            [IO.File]::WriteAllText($setupApplyTemporary, ($setupMergedApply | ConvertTo-Json -Depth 100), [Text.UTF8Encoding]::new($false))
            [IO.File]::Move($setupApplyTemporary, $setupApplyPath, $true)
        }
        $setupResult.phase = 'ssh-user-directory-access'
        $setupRootAcl = Get-Acl -LiteralPath 'C:\WRE'
        $setupRootAcl.SetAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
            $setupUser.SID, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
        Set-Acl -LiteralPath 'C:\WRE' -AclObject $setupRootAcl
        $setupResult.phase = 'service-and-listener'
        & $setupSshd -t
        if ($LASTEXITCODE -ne 0) { throw 'sshd configuration validation failed.' }
        if ((Get-Service sshd).Status -eq 'Running') { Restart-Service sshd } else { Start-Service sshd }
        $setupListeners = @()
        for ($setupAttempt = 0; $setupAttempt -lt 20; $setupAttempt++) {
            $setupListeners = @(Get-NetTCPConnection -LocalPort 22 -State Listen -ErrorAction SilentlyContinue)
            if ($setupListeners.Count -gt 0) { break }
            Start-Sleep -Milliseconds 500
        }
        $setupAddresses = @($setupListeners.LocalAddress | Select-Object -Unique)
        if ($setupAddresses.Count -ne 1 -or $setupAddresses[0] -ne $setupListen) { throw 'Active SSH listener does not exactly match the Tailscale policy.' }
        $setupResult.activeListenAddresses = $setupAddresses
        $setupResult.phase = 'scheduled-tasks'
        foreach ($setupTaskName in @('WRE Sshd Repair Logon', 'WRE Sshd Repair Startup', 'WRE Sshd Repair Watch', 'WRE WRE Apply')) {
            $setupTask = Get-ScheduledTask -TaskName $setupTaskName -ErrorAction Stop
            if ($setupTask.State -eq 'Disabled' -or $setupTask.Principal.UserId -notin @('SYSTEM', 'S-1-5-18', 'NT AUTHORITY\SYSTEM')) {
                throw "Required enabled SYSTEM task was not verified: $setupTaskName"
            }
        }
        $setupResult.success = $true
        $setupResult.phase = 'complete'
        $setupExit = 0
    }
} catch {
    $setupResult.error = $_.Exception.Message
    [Console]::Error.WriteLine('FAILED: ' + $_.Exception.Message)
} finally {
    if ($setupTranscript) { Stop-Transcript | Out-Null }
    if (-not $CheckOnly) {
        $setupReportName = 'onboarding-result-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fffffff') + '.json'
        try {
            if (-not (Test-Path -LiteralPath 'C:\WRE\logs')) { throw 'Use the current-user report directory.' }
            $setupResult.report = Join-Path 'C:\WRE\logs' $setupReportName
            [IO.File]::WriteAllText($setupResult.report, ($setupResult | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
        } catch {
            $setupResult.report = Join-Path ([IO.Path]::GetTempPath()) $setupReportName
            [IO.File]::WriteAllText($setupResult.report, ($setupResult | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
        }
    }
    $setupResult | ConvertTo-Json -Depth 8
}
exit $setupExit
