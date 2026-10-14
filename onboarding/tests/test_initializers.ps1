#requires -Version 7.0
[CmdletBinding()]
param(
    [string]$PwshInitializer = (Join-Path $PSScriptRoot '../initialize-pwsh7.ps1'),
    [string]$WreInitializer = (Join-Path $PSScriptRoot '../initialize-wre.ps1')
)
$ErrorActionPreference = 'Stop'
function Test-WreCondition {
    param([bool]$Value, [string]$Message)
    if (-not $Value) { throw $Message }
}
$qaRoot = Join-Path ([IO.Path]::GetTempPath()) ('wre-onboarding-test-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $qaRoot | Out-Null
$qaChecks = @()
try {
    if ($IsWindows) {
        $qaSettingsPath = Join-Path $qaRoot 'settings-保留.json'
        $qaCoreGuid = '{574e775e-4f2a-5b96-ac1e-a2962a402336}'
        $qaOriginal = [ordered]@{
            defaultProfile = '{61c54bbd-c2c6-5271-96e7-009a87ff44bf}'
            profiles = @{list = @(@{guid = $qaCoreGuid; name = 'PowerShell'; source = 'Windows.Terminal.PowershellCore'; hidden = $false})}
            unrelatedSetting = @{nested = @('保留', 'value'); number = 42; enabled = $true}
        }
        [IO.File]::WriteAllText($qaSettingsPath, ($qaOriginal | ConvertTo-Json -Depth 10), [Text.UTF8Encoding]::new($true))
        $qaOriginalBytes = [IO.File]::ReadAllBytes($qaSettingsPath)
        $qaExe = (Get-Process -Id $PID).Path
        $qaDry = & $qaExe -NoLogo -NoProfile -NonInteractive -File $PwshInitializer -TerminalSettingsPath $qaSettingsPath -CheckOnly
        Test-WreCondition ($LASTEXITCODE -eq 0) 'Terminal dry-run failed.'
        Test-WreCondition (([Convert]::ToBase64String([IO.File]::ReadAllBytes($qaSettingsPath))) -eq ([Convert]::ToBase64String($qaOriginalBytes))) 'Dry-run changed settings.'
        $qaDryResult = ($qaDry -join [Environment]::NewLine) | ConvertFrom-Json
        Test-WreCondition ([bool]$qaDryResult.terminals[0].wouldChange -and -not $qaDryResult.terminals[0].changed) 'Dry-run did not describe the planned default switch.'
        $qaChecks += 'terminal-check-only-no-writes'
        $qaApplied = & $qaExe -NoLogo -NoProfile -NonInteractive -File $PwshInitializer -TerminalSettingsPath $qaSettingsPath
        Test-WreCondition ($LASTEXITCODE -eq 0) 'Terminal apply failed.'
        $qaAppliedResult = ($qaApplied -join [Environment]::NewLine) | ConvertFrom-Json
        $qaAfter = Get-Content -LiteralPath $qaSettingsPath -Raw | ConvertFrom-Json -AsHashtable
        Test-WreCondition ($qaAfter.defaultProfile -eq $qaCoreGuid) 'Terminal default was not selected.'
        $qaOriginal.defaultProfile = $qaCoreGuid
        Test-WreCondition (($qaAfter | ConvertTo-Json -Depth 10 -Compress) -eq ($qaOriginal | ConvertTo-Json -Depth 10 -Compress)) 'Unrelated settings changed.'
        Test-WreCondition (([Convert]::ToBase64String([IO.File]::ReadAllBytes($qaAppliedResult.terminals[0].backup))) -eq ([Convert]::ToBase64String($qaOriginalBytes))) 'Backup bytes differ.'
        Test-WreCondition ([IO.File]::ReadAllBytes($qaSettingsPath)[0] -eq 239) 'UTF-8 BOM was not preserved.'
        $qaChecks += 'terminal-apply-backup-unicode-bom-preserved'
        $qaRepeated = & $qaExe -NoLogo -NoProfile -NonInteractive -File $PwshInitializer -TerminalSettingsPath $qaSettingsPath
        Test-WreCondition ($LASTEXITCODE -eq 0) 'Terminal rerun failed.'
        $qaRepeatedResult = ($qaRepeated -join [Environment]::NewLine) | ConvertFrom-Json
        Test-WreCondition (-not $qaRepeatedResult.terminals[0].changed) 'Terminal rerun was not idempotent.'
        $qaChecks += 'terminal-idempotent-rerun'
        $qaAdmin = [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
        $qaAcl = [Security.AccessControl.FileSecurity]::new()
        $qaAcl.SetAccessRuleProtection($true, $false)
        $qaAcl.SetOwner($qaAdmin)
        $qaAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($qaAdmin, 'FullControl', 'Allow'))
        $qaRule = [Security.AccessControl.FileSystemAccessRule]::new($qaAdmin, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        Test-WreCondition ($qaRule.InheritanceFlags.ToString() -match 'ContainerInherit') 'Directory ACL flags were not constructed.'
        $qaChecks += 'windows-sid-acl-constructors-in-memory'
    }
    $qaTokens = $null
    $qaErrors = $null
    $qaAst = [Management.Automation.Language.Parser]::ParseFile($WreInitializer, [ref]$qaTokens, [ref]$qaErrors)
    Test-WreCondition (-not $qaErrors) 'WRE initializer syntax is invalid.'
    $qaFunction = $qaAst.Find({param($qaNode) $qaNode -is [Management.Automation.Language.FunctionDefinitionAst] -and $qaNode.Name -eq 'Merge-WreApplySpec'}, $true)
    Test-WreCondition ($null -ne $qaFunction) 'Apply-spec merge function is missing.'
    . ([scriptblock]::Create($qaFunction.Extent.Text))
    $qaBusiness = [ordered]@{
        expectedListenAddress = '192.168.50.20'
        ensureRepairTasks = $false
        streamserv = @{root = 'D:/business'; runAsUser = 'SYSTEM'}
        tasks = @(@{name = 'Business task'; args = @('value')})
        futureField = @{enabled = $true}
    }
    $qaMerged = Merge-WreApplySpec -Existing $qaBusiness -ListenAddress '100.64.0.1'
    Test-WreCondition ($qaMerged.expectedListenAddress -eq '100.64.0.1' -and $qaMerged.ensureRepairTasks) 'Core apply-spec metadata was not aligned.'
    Test-WreCondition (($qaMerged.streamserv | ConvertTo-Json -Compress) -eq ($qaBusiness.streamserv | ConvertTo-Json -Compress)) 'StreamServ configuration changed.'
    Test-WreCondition (($qaMerged.tasks | ConvertTo-Json -Depth 10 -Compress) -eq ($qaBusiness.tasks | ConvertTo-Json -Depth 10 -Compress)) 'Business tasks changed.'
    Test-WreCondition ($qaMerged.futureField.enabled) 'Future apply-spec fields were lost.'
    Test-WreCondition ($qaBusiness.expectedListenAddress -eq '192.168.50.20') 'Merge mutated the original metadata.'
    $qaChecks += 'apply-spec-business-and-future-fields-preserved'
    @{passed = $true; version = $PSVersionTable.PSVersion.ToString(); checks = $qaChecks} | ConvertTo-Json -Depth 4
} finally {
    Remove-Item -LiteralPath $qaRoot -Recurse -Force
}
