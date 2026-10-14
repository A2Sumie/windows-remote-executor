#requires -Version 7.0
<#
.SYNOPSIS
Initialize PowerShell 7, optionally install PSScriptAnalyzer, and select its Windows Terminal profile.
.DESCRIPTION
Run as the Windows Terminal user. -CheckOnly performs no configuration writes or module installs.
#>
[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$SkipTerminal,
    [switch]$InstallAnalyzer,
    [string]$TerminalSettingsPath
)
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSEdition -ne 'Core') { throw 'PowerShell 7 Core is required.' }
$initResult = [ordered]@{
    version = $PSVersionTable.PSVersion.ToString()
    edition = $PSVersionTable.PSEdition
    executable = (Get-Process -Id $PID).Path
    checkOnly = [bool]$CheckOnly
    terminals = @()
}
$initAnalyzer = Get-Module -ListAvailable -Name PSScriptAnalyzer |
    Sort-Object Version -Descending | Select-Object -First 1
if (-not $initAnalyzer -and $InstallAnalyzer) {
    if ($CheckOnly) { throw 'PSScriptAnalyzer is missing; rerun without -CheckOnly to install it.' }
    Install-Module PSScriptAnalyzer -RequiredVersion 1.25.0 -Repository PSGallery -Scope CurrentUser -Force
    $initAnalyzer = Get-Module -ListAvailable -Name PSScriptAnalyzer |
        Sort-Object Version -Descending | Select-Object -First 1
    if (-not $initAnalyzer) { throw 'PSScriptAnalyzer was not found after installation.' }
}
$initResult.analyzerVersion = if ($initAnalyzer) { $initAnalyzer.Version.ToString() } else { $null }
if ($IsWindows -and -not $SkipTerminal) {
    $initPaths = if ($TerminalSettingsPath) {
        @($TerminalSettingsPath)
    } else {
        @(
            (Join-Path $env:LOCALAPPDATA 'Packages/Microsoft.WindowsTerminal_8wekyb3d8bbwe/LocalState/settings.json'),
            (Join-Path $env:LOCALAPPDATA 'Packages/Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe/LocalState/settings.json'),
            (Join-Path $env:LOCALAPPDATA 'Microsoft/Windows Terminal/settings.json')
        )
    }
    foreach ($initPath in $initPaths) {
        if (-not (Test-Path -LiteralPath $initPath -PathType Leaf)) { continue }
        $initOriginalBytes = [IO.File]::ReadAllBytes($initPath)
        $initOriginal = [IO.File]::ReadAllText($initPath)
        $initSettings = ConvertFrom-Json -InputObject $initOriginal -AsHashtable
        $initProfiles = if ($initSettings.profiles -is [System.Collections.IDictionary]) {
            @($initSettings.profiles.list)
        } else { @($initSettings.profiles) }
        $initCore = @($initProfiles | Where-Object {
            $_.source -eq 'Windows.Terminal.PowershellCore' -and -not $_.hidden -and $_.name -notmatch 'Preview'
        })
        if ($initCore.Count -ne 1 -or -not $initCore[0].guid) {
            throw "No unique enabled PowerShell Core profile in $initPath. Open Windows Terminal once after installing pwsh."
        }
        $initGuid = [string]$initCore[0].guid
        $initEntry = [ordered]@{path = $initPath; before = $initSettings.defaultProfile; after = $initGuid; changed = $false}
        if ($initSettings.defaultProfile -ne $initGuid) {
            $initPattern = [regex]'("defaultProfile"\s*:\s*)"[^"]*"'
            if ($initPattern.Matches($initOriginal).Count -ne 1) { throw 'Expected one defaultProfile field.' }
            $initUpdated = $initPattern.Replace($initOriginal, {param($initMatch) $initMatch.Groups[1].Value + '"' + $initGuid + '"'})
            $initExpected = ConvertFrom-Json -InputObject $initOriginal -AsHashtable
            $initExpected.defaultProfile = $initGuid
            $initActual = ConvertFrom-Json -InputObject $initUpdated -AsHashtable
            if (($initExpected | ConvertTo-Json -Depth 100 -Compress) -ne ($initActual | ConvertTo-Json -Depth 100 -Compress)) {
                throw 'Unexpected unrelated Terminal settings change.'
            }
            $initEntry.wouldChange = $true
            if (-not $CheckOnly) {
                $initStamp = Get-Date -Format 'yyyyMMdd-HHmmss-fffffff'
                $initBackup = $initPath + '.before-wre-pwsh7-' + $initStamp
                [IO.File]::WriteAllBytes($initBackup, $initOriginalBytes)
                if ([Convert]::ToBase64String([IO.File]::ReadAllBytes($initPath)) -ne [Convert]::ToBase64String($initOriginalBytes)) {
                    throw 'Terminal settings changed concurrently; rerun the initializer.'
                }
                $initBom = $initOriginalBytes.Length -ge 3 -and $initOriginalBytes[0] -eq 239 -and $initOriginalBytes[1] -eq 187 -and $initOriginalBytes[2] -eq 191
                $initTemporary = $initPath + '.wre-pwsh7-' + $initStamp + '.tmp'
                [IO.File]::WriteAllText($initTemporary, $initUpdated, [Text.UTF8Encoding]::new($initBom))
                [IO.File]::Move($initTemporary, $initPath, $true)
                $initReadback = Get-Content -LiteralPath $initPath -Raw | ConvertFrom-Json -AsHashtable
                if (($initReadback | ConvertTo-Json -Depth 100 -Compress) -ne ($initExpected | ConvertTo-Json -Depth 100 -Compress)) {
                    throw 'Terminal settings readback mismatch; restore the reported backup.'
                }
                $initEntry.changed = $true
                $initEntry.backup = $initBackup
            }
        }
        $initResult.terminals += $initEntry
    }
    if ($initResult.terminals.Count -eq 0) { $initResult.terminalStatus = 'No existing Windows Terminal settings; open Terminal once and rerun.' }
}
$initResult | ConvertTo-Json -Depth 6
