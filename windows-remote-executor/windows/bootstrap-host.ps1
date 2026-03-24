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

$ErrorActionPreference = 'Stop'

function Get-IPv4Priority {
    param([string]$Address)

    $octets = $Address.Split('.')
    if ($octets.Count -ne 4) {
        return 100
    }

    $first = [int]$octets[0]
    $second = [int]$octets[1]

    if ($first -eq 100 -and $second -ge 64 -and $second -le 127) {
        return 0
    }

    if ($first -eq 10) {
        return 1
    }

    if ($first -eq 192 -and $second -eq 168) {
        return 2
    }

    if ($first -eq 127) {
        return 3
    }

    if ($first -eq 169 -and $second -eq 254) {
        return 4
    }

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

$invoke = @{
    TargetUser = $TargetUser
    ListenAddress = Resolve-ListenAddress
    CodexRoot = $CodexRoot
}

if ($PublicKeyPath) {
    $invoke.PublicKeyPath = $PublicKeyPath
}

if ($AuthorizedKey) {
    $invoke.AuthorizedKey = $AuthorizedKey
}

if ($SetPowerShellDefaultShell) {
    $invoke.SetPowerShellDefaultShell = $true
}

if ($InstallTailscale) {
    $invoke.InstallTailscale = $true
}

& "$PSScriptRoot\install-openssh-executor.ps1" @invoke
