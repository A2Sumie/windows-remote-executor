$ErrorActionPreference = 'Stop'

function Find-Tool {
    param([string]$Name)

    $cmd = Get-Command -Name $Name -ErrorAction SilentlyContinue
    if ($null -eq $cmd) {
        return $null
    }

    return $cmd.Source
}

function Get-ServiceProbe {
    param([string]$Name)

    $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if ($null -eq $service) {
        return $null
    }

    $serviceCim = Get-CimInstance Win32_Service -Filter "Name='$Name'" -ErrorAction SilentlyContinue
    $delayedAutoStart = $null
    try {
        $reg = Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Services\$Name" -Name DelayedAutostart -ErrorAction SilentlyContinue
        if ($null -ne $reg) {
            $delayedAutoStart = [bool]$reg.DelayedAutostart
        }
    } catch {
    }

    return @{
        status = [string]$service.Status
        start_type = [string]$service.StartType
        start_mode = if ($serviceCim) { [string]$serviceCim.StartMode } else { $null }
        delayed_auto_start = $delayedAutoStart
        start_name = if ($serviceCim) { [string]$serviceCim.StartName } else { $null }
        dependencies = @($service.ServicesDependedOn | ForEach-Object { $_.Name })
    }
}

$os = Get-CimInstance Win32_OperatingSystem
$computer = Get-CimInstance Win32_ComputerSystem
$network = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike '127.*' } |
    Select-Object InterfaceAlias, IPAddress, PrefixLength
$disks = Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" |
    Select-Object DeviceID, VolumeName, Size, FreeSpace
$services = @('sshd', 'Tailscale')
$serviceMap = @{}

foreach ($serviceName in $services) {
    $serviceProbe = Get-ServiceProbe -Name $serviceName
    if ($null -ne $serviceProbe) {
        $serviceMap[$serviceName] = $serviceProbe
    }
}

$toolNames = @(
    'powershell',
    'pwsh',
    'git',
    'ssh',
    'scp',
    'tar',
    'winget',
    'choco',
    'python',
    'py',
    'node',
    'npm',
    'bun',
    'docker',
    'ffmpeg',
    'ffprobe'
)

$tools = @{}
foreach ($toolName in $toolNames) {
    $tools[$toolName] = Find-Tool -Name $toolName
}

$bootTime = [DateTime]$os.LastBootUpTime
$now = Get-Date
$probe = [ordered]@{
    timestamp = $now.ToString('o')
    hostname = $env:COMPUTERNAME
    current_user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    os = @{
        caption = $os.Caption
        version = $os.Version
        build_number = $os.BuildNumber
        architecture = $os.OSArchitecture
        last_boot = $bootTime.ToString('o')
        uptime_hours = [Math]::Round((New-TimeSpan -Start $bootTime -End $now).TotalHours, 2)
        timezone = (Get-TimeZone).Id
    }
    hardware = @{
        manufacturer = $computer.Manufacturer
        model = $computer.Model
        logical_processors = $computer.NumberOfLogicalProcessors
        total_memory_gb = [Math]::Round($computer.TotalPhysicalMemory / 1GB, 2)
    }
    ssh = @{
        port = 22
        services = $serviceMap
    }
    network = $network
    disks = $disks
    tools = $tools
    execution_policy = @{
        machine_policy = (Get-ExecutionPolicy -Scope MachinePolicy)
        user_policy = (Get-ExecutionPolicy -Scope UserPolicy)
        process = (Get-ExecutionPolicy -Scope Process)
        current_user = (Get-ExecutionPolicy -Scope CurrentUser)
        local_machine = (Get-ExecutionPolicy -Scope LocalMachine)
    }
}

$probe | ConvertTo-Json -Depth 6 -Compress
