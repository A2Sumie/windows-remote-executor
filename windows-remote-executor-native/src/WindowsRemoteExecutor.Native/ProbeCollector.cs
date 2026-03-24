using System.Management;
using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using Microsoft.Win32;

namespace WindowsRemoteExecutor.Native;

internal sealed class ProbeResult
{
    public string Timestamp { get; init; } = string.Empty;
    public string Hostname { get; init; } = Environment.MachineName;
    public string CurrentUser { get; init; } = Environment.UserName;
    public OsProbe Os { get; init; } = new();
    public HardwareProbe Hardware { get; init; } = new();
    public SshProbe Ssh { get; init; } = new();
    public IReadOnlyList<NetworkProbe> Network { get; init; } = Array.Empty<NetworkProbe>();
    public IReadOnlyList<DiskProbe> Disks { get; init; } = Array.Empty<DiskProbe>();
    public Dictionary<string, string?> Tools { get; init; } = new(StringComparer.OrdinalIgnoreCase);
}

internal sealed class OsProbe
{
    public string Caption { get; init; } = string.Empty;
    public string Version { get; init; } = string.Empty;
    public string BuildNumber { get; init; } = string.Empty;
    public string Architecture { get; init; } = string.Empty;
    public string LastBoot { get; init; } = string.Empty;
    public double UptimeHours { get; init; }
    public string Timezone { get; init; } = string.Empty;
}

internal sealed class HardwareProbe
{
    public string Manufacturer { get; init; } = string.Empty;
    public string Model { get; init; } = string.Empty;
    public int LogicalProcessors { get; init; }
    public double TotalMemoryGb { get; init; }
}

internal sealed class SshProbe
{
    public int Port { get; init; } = 22;
    public Dictionary<string, ProbeServiceState> Services { get; init; } = new(StringComparer.OrdinalIgnoreCase);
    public IReadOnlyList<string> ConfiguredListenAddresses { get; init; } = Array.Empty<string>();
    public IReadOnlyList<string> ActiveListenAddresses { get; init; } = Array.Empty<string>();
    public string ExposurePolicyLabel { get; init; } = "UNCONFIGURED";
    public string ExposureMode { get; init; } = "private-only";
    public bool AccessTokenRequired { get; init; }
}

internal sealed class ProbeServiceState
{
    public string Status { get; init; } = string.Empty;
    public string StartType { get; init; } = string.Empty;
    public string StartMode { get; init; } = string.Empty;
    public bool? DelayedAutoStart { get; init; }
    public string StartName { get; init; } = string.Empty;
    public IReadOnlyList<string> Dependencies { get; init; } = Array.Empty<string>();
    public string State { get; init; } = string.Empty;
}

internal sealed class NetworkProbe
{
    public string InterfaceAlias { get; init; } = string.Empty;
    public string IpAddress { get; init; } = string.Empty;
    public int PrefixLength { get; init; }
}

internal sealed class DiskProbe
{
    public string DeviceId { get; init; } = string.Empty;
    public string VolumeName { get; init; } = string.Empty;
    public long Size { get; init; }
    public long FreeSpace { get; init; }
}

internal static class ProbeCollector
{
    private static readonly string[] ToolNames =
    [
        "powershell",
        "pwsh",
        "git",
        "ssh",
        "scp",
        "tar",
        "winget",
        "choco",
        "everything",
        "python",
        "py",
        "node",
        "npm",
        "bun",
        "docker",
        "ffmpeg",
        "ffprobe"
    ];

    public static ProbeResult Collect()
    {
        var os = QuerySingle("SELECT Caption, Version, BuildNumber, OSArchitecture, LastBootUpTime FROM Win32_OperatingSystem");
        var computer = QuerySingle("SELECT Manufacturer, Model, NumberOfLogicalProcessors, TotalPhysicalMemory FROM Win32_ComputerSystem");
        var boot = ManagementDateTimeConverter.ToDateTime(os["LastBootUpTime"]?.ToString() ?? string.Empty);
        var now = DateTimeOffset.Now;

        var services = new Dictionary<string, ProbeServiceState>(StringComparer.OrdinalIgnoreCase);
        foreach (var serviceName in new[] { "sshd", "Tailscale" })
        {
            var state = TryGetService(serviceName);
            if (state is not null)
            {
                services[serviceName] = new ProbeServiceState
                {
                    Status = state.State,
                    StartType = state.StartMode,
                    StartMode = state.StartMode,
                    DelayedAutoStart = ReadDelayedAutoStart(serviceName),
                    StartName = state.StartName,
                    Dependencies = ReadDependencies(serviceName),
                    State = state.State
                };
            }
        }

        var policy = AccessPolicy.TryLoadDefault();
        var configuredListenAddresses = ReadConfiguredSshListenAddresses();
        var activeListenAddresses = GetActiveTcpListeners(22)
            .Select(endpoint => endpoint.Address.ToString())
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .OrderBy(value => value, StringComparer.OrdinalIgnoreCase)
            .ToArray();

        return new ProbeResult
        {
            Timestamp = now.ToString("o"),
            Hostname = Environment.MachineName,
            CurrentUser = WindowsIdentityWrapper.CurrentName(),
            Os = new OsProbe
            {
                Caption = os["Caption"]?.ToString() ?? string.Empty,
                Version = os["Version"]?.ToString() ?? string.Empty,
                BuildNumber = os["BuildNumber"]?.ToString() ?? string.Empty,
                Architecture = os["OSArchitecture"]?.ToString() ?? string.Empty,
                LastBoot = new DateTimeOffset(boot).ToString("o"),
                UptimeHours = Math.Round((now - new DateTimeOffset(boot)).TotalHours, 2),
                Timezone = TimeZoneInfo.Local.Id
            },
            Hardware = new HardwareProbe
            {
                Manufacturer = computer["Manufacturer"]?.ToString() ?? string.Empty,
                Model = computer["Model"]?.ToString() ?? string.Empty,
                LogicalProcessors = Convert.ToInt32(computer["NumberOfLogicalProcessors"] ?? 0),
                TotalMemoryGb = Math.Round(Convert.ToDouble(computer["TotalPhysicalMemory"] ?? 0d) / (1024 * 1024 * 1024), 2)
            },
            Ssh = new SshProbe
            {
                Port = 22,
                Services = services,
                ConfiguredListenAddresses = configuredListenAddresses,
                ActiveListenAddresses = activeListenAddresses,
                ExposurePolicyLabel = policy?.Label ?? "UNCONFIGURED",
                ExposureMode = policy?.ExposureMode ?? "private-only",
                AccessTokenRequired = policy?.AccessTokenRequired ?? false
            },
            Network = CollectNetworks(),
            Disks = CollectDisks(),
            Tools = ToolNames.ToDictionary(name => name, TryFindCommand, StringComparer.OrdinalIgnoreCase)
        };
    }

    public static Win32ServiceInfo? TryGetService(string name)
    {
        var safeName = name.Replace("'", "''");
        using var searcher = new ManagementObjectSearcher(
            "root\\cimv2",
            $"SELECT Name, State, StartMode, StartName FROM Win32_Service WHERE Name = '{safeName}'");
        using var results = searcher.Get();
        foreach (var item in results.Cast<ManagementObject>())
        {
            return new Win32ServiceInfo
            {
                Name = item["Name"]?.ToString() ?? name,
                State = item["State"]?.ToString() ?? string.Empty,
                StartMode = item["StartMode"]?.ToString() ?? string.Empty,
                StartName = item["StartName"]?.ToString() ?? string.Empty
            };
        }

        return null;
    }

    public static string? TryFindCommand(string name)
    {
        var builtin = TryFindKnownInstall(name);
        if (!string.IsNullOrWhiteSpace(builtin))
        {
            return builtin;
        }

        var path = Environment.GetEnvironmentVariable("PATH");
        if (string.IsNullOrWhiteSpace(path))
        {
            return null;
        }

        var extensions = (Environment.GetEnvironmentVariable("PATHEXT") ?? ".EXE;.CMD;.BAT;.PS1")
            .Split(';', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);

        foreach (var dir in path.Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
        {
            foreach (var candidate in EnumerateCandidates(dir, name, extensions))
            {
                if (File.Exists(candidate))
                {
                    return candidate;
                }
            }
        }

        return null;
    }

    public static IReadOnlyList<string> ReadConfiguredSshListenAddresses()
    {
        var configPath = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),
            "ssh",
            "sshd_config");

        if (!File.Exists(configPath))
        {
            return Array.Empty<string>();
        }

        var addresses = new List<string>();
        foreach (var rawLine in File.ReadAllLines(configPath))
        {
            var line = rawLine.Trim();
            if (line.Length == 0 || line.StartsWith("#", StringComparison.Ordinal))
            {
                continue;
            }

            if (!line.StartsWith("ListenAddress ", StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }

            var token = line["ListenAddress ".Length..].Trim();
            var commentIndex = token.IndexOf('#');
            if (commentIndex >= 0)
            {
                token = token[..commentIndex].Trim();
            }

            var address = NormalizeListenAddress(token);
            if (!string.IsNullOrWhiteSpace(address))
            {
                addresses.Add(address);
            }
        }

        return addresses
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .OrderBy(value => value, StringComparer.OrdinalIgnoreCase)
            .ToArray();
    }

    public static IReadOnlyList<IPEndPoint> GetActiveTcpListeners(int port)
    {
        return IPGlobalProperties.GetIPGlobalProperties()
            .GetActiveTcpListeners()
            .Where(endpoint => endpoint.Port == port)
            .OrderBy(endpoint => endpoint.Address.ToString(), StringComparer.OrdinalIgnoreCase)
            .ToArray();
    }

    private static string? TryFindKnownInstall(string name)
    {
        if (name.Equals("everything", StringComparison.OrdinalIgnoreCase) ||
            name.Equals("everything.exe", StringComparison.OrdinalIgnoreCase))
        {
            foreach (var candidate in new[]
            {
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Everything", "Everything.exe"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86), "Everything", "Everything.exe")
            })
            {
                if (File.Exists(candidate))
                {
                    return candidate;
                }
            }
        }

        return null;
    }

    private static IEnumerable<string> EnumerateCandidates(string directory, string name, string[] extensions)
    {
        if (Path.IsPathRooted(name))
        {
            yield return name;
            yield break;
        }

        if (Path.HasExtension(name))
        {
            yield return Path.Combine(directory, name);
            yield break;
        }

        foreach (var extension in extensions)
        {
            yield return Path.Combine(directory, name + extension.ToLowerInvariant());
            yield return Path.Combine(directory, name + extension.ToUpperInvariant());
        }

        yield return Path.Combine(directory, name);
    }

    private static IReadOnlyList<NetworkProbe> CollectNetworks()
    {
        var list = new List<NetworkProbe>();
        foreach (var network in NetworkInterface.GetAllNetworkInterfaces())
        {
            foreach (var unicast in network.GetIPProperties().UnicastAddresses)
            {
                if (unicast.Address.AddressFamily != AddressFamily.InterNetwork)
                {
                    continue;
                }

                if (unicast.Address.ToString().StartsWith("127.", StringComparison.Ordinal))
                {
                    continue;
                }

                list.Add(new NetworkProbe
                {
                    InterfaceAlias = network.Name,
                    IpAddress = unicast.Address.ToString(),
                    PrefixLength = unicast.PrefixLength
                });
            }
        }

        return list;
    }

    private static IReadOnlyList<DiskProbe> CollectDisks()
    {
        return DriveInfo.GetDrives()
            .Where(drive => drive.DriveType == DriveType.Fixed && drive.IsReady)
            .Select(drive => new DiskProbe
            {
                DeviceId = drive.Name.TrimEnd('\\'),
                VolumeName = drive.VolumeLabel,
                Size = drive.TotalSize,
                FreeSpace = drive.AvailableFreeSpace
            })
            .ToArray();
    }

    private static bool? ReadDelayedAutoStart(string serviceName)
    {
        using var key = Registry.LocalMachine.OpenSubKey($@"SYSTEM\CurrentControlSet\Services\{serviceName}", writable: false);
        var value = key?.GetValue("DelayedAutostart");
        if (value is null)
        {
            return null;
        }

        return Convert.ToInt32(value) != 0;
    }

    private static IReadOnlyList<string> ReadDependencies(string serviceName)
    {
        using var key = Registry.LocalMachine.OpenSubKey($@"SYSTEM\CurrentControlSet\Services\{serviceName}", writable: false);
        var value = key?.GetValue("DependOnService");
        return value switch
        {
            string single => new[] { single },
            string[] many => many.Where(item => !string.IsNullOrWhiteSpace(item)).ToArray(),
            _ => Array.Empty<string>()
        };
    }

    private static ManagementBaseObject QuerySingle(string query)
    {
        using var searcher = new ManagementObjectSearcher("root\\cimv2", query);
        using var results = searcher.Get();
        return results.Cast<ManagementBaseObject>().First();
    }

    private static string NormalizeListenAddress(string value)
    {
        if (value.StartsWith("[", StringComparison.Ordinal))
        {
            var closing = value.IndexOf(']');
            if (closing > 1)
            {
                return value[1..closing];
            }
        }

        var colonCount = value.Count(ch => ch == ':');
        if (colonCount == 1 && value.Contains('.', StringComparison.Ordinal))
        {
            var lastColon = value.LastIndexOf(':');
            if (lastColon > 0)
            {
                return value[..lastColon];
            }
        }

        return value;
    }
}

internal sealed class Win32ServiceInfo
{
    public string Name { get; init; } = string.Empty;
    public string State { get; init; } = string.Empty;
    public string StartMode { get; init; } = string.Empty;
    public string StartName { get; init; } = string.Empty;
}

internal static class WindowsIdentityWrapper
{
    public static string CurrentName()
    {
        using var identity = System.Security.Principal.WindowsIdentity.GetCurrent();
        return identity.Name ?? Environment.UserName;
    }
}
