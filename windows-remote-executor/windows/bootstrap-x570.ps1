[CmdletBinding()]
param(
    [string]$PublicKeyPath,
    [string]$AuthorizedKey,
    [string]$TargetUser = $env:USERNAME,
    [string]$ListenAddress,
    [string]$CodexRoot = 'C:\CodexRemote',
    [switch]$SetPowerShellDefaultShell,
    [switch]$InstallTailscale
)

& "$PSScriptRoot\bootstrap-host.ps1" @PSBoundParameters
