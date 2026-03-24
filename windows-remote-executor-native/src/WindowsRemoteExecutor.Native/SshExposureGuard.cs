using System.Net;
using System.Text;
using System.Text.Json;

namespace WindowsRemoteExecutor.Native;

internal sealed class SshGuardOptions
{
    public string? ExpectedListenAddress { get; init; }
    public string? LogPath { get; init; }
    public bool DisableOnUnsafe { get; init; } = true;

    public static SshGuardOptions FromArgs(string[] args)
    {
        string? expectedListenAddress = null;
        string? logPath = null;
        var disableOnUnsafe = true;

        for (var i = 0; i < args.Length; i++)
        {
            switch (args[i])
            {
                case "--expected-listen-address":
                    if (i + 1 >= args.Length)
                    {
                        throw new ArgumentException("--expected-listen-address requires a value.");
                    }

                    expectedListenAddress = args[++i];
                    break;

                case "--log-path":
                    if (i + 1 >= args.Length)
                    {
                        throw new ArgumentException("--log-path requires a value.");
                    }

                    logPath = args[++i];
                    break;

                case "--no-disable":
                    disableOnUnsafe = false;
                    break;

                default:
                    throw new ArgumentException($"Unknown guard option: {args[i]}");
            }
        }

        return new SshGuardOptions
        {
            ExpectedListenAddress = expectedListenAddress,
            LogPath = logPath,
            DisableOnUnsafe = disableOnUnsafe
        };
    }
}

internal sealed class SshGuardResult
{
    public string Timestamp { get; init; } = DateTimeOffset.Now.ToString("o");
    public string PolicyLabel { get; init; } = "UNCONFIGURED";
    public string ExposureMode { get; init; } = "private-only";
    public bool AccessTokenRequired { get; init; }
    public string ExpectedListenAddress { get; init; } = string.Empty;
    public IReadOnlyList<string> ConfiguredListenAddresses { get; init; } = Array.Empty<string>();
    public IReadOnlyList<string> ActiveListenAddresses { get; init; } = Array.Empty<string>();
    public bool Safe { get; init; }
    public bool SshdDisabled { get; init; }
    public IReadOnlyList<string> Problems { get; init; } = Array.Empty<string>();
    public string? LogPath { get; init; }
}

internal static class SshExposureGuard
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        WriteIndented = true
    };

    public static async Task<int> RunCommandAsync(string[] args)
    {
        var result = await EvaluateAsync(SshGuardOptions.FromArgs(args));
        Console.WriteLine(JsonSerializer.Serialize(result, JsonOptions));
        return result.Safe ? 0 : 3;
    }

    public static async Task<SshGuardResult> EvaluateAsync(SshGuardOptions options)
    {
        var policy = AccessPolicy.TryLoadDefault();
        var expected = options.ExpectedListenAddress ?? policy?.ExpectedListenAddress ?? string.Empty;
        var logPath = options.LogPath ?? GetDefaultLogPath();

        var configured = ProbeCollector.ReadConfiguredSshListenAddresses();
        var active = ProbeCollector.GetActiveTcpListeners(22)
            .Select(endpoint => endpoint.Address.ToString())
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .OrderBy(value => value, StringComparer.OrdinalIgnoreCase)
            .ToArray();

        var problems = new List<string>();
        if (string.IsNullOrWhiteSpace(expected))
        {
            problems.Add("Expected listen address is missing.");
        }

        IPAddress? expectedAddress = null;
        if (!string.IsNullOrWhiteSpace(expected) && !IPAddress.TryParse(expected, out expectedAddress))
        {
            problems.Add($"Expected listen address is not a valid IP: {expected}");
        }

        if (expectedAddress is not null &&
            policy?.AllowsPublicExposure != true &&
            !NetworkSafety.IsExpectedPrivateAddress(expectedAddress))
        {
            problems.Add($"Expected listen address is outside the allowed private ranges: {expected}");
        }

        if (policy?.AllowsPublicExposure == true && !policy.AccessTokenRequired)
        {
            problems.Add("Public exposure mode requires an access token.");
        }

        if (configured.Count == 0)
        {
            problems.Add("sshd_config has no explicit ListenAddress.");
        }

        foreach (var configuredAddress in configured)
        {
            if (!IPAddress.TryParse(configuredAddress, out var parsed))
            {
                problems.Add($"Configured ListenAddress is not a plain IP: {configuredAddress}");
                continue;
            }

            if (expectedAddress is not null && !parsed.Equals(expectedAddress))
            {
                problems.Add($"Configured ListenAddress does not match the expected address: {configuredAddress}");
            }
        }

        foreach (var activeAddress in active)
        {
            if (!IPAddress.TryParse(activeAddress, out var parsed))
            {
                problems.Add($"Active sshd listener is not a plain IP: {activeAddress}");
                continue;
            }

            if (parsed.Equals(IPAddress.Any) || parsed.Equals(IPAddress.IPv6Any))
            {
                problems.Add($"sshd is listening on a wildcard address: {activeAddress}");
                continue;
            }

            if (expectedAddress is not null && !IPAddress.IsLoopback(parsed) && !parsed.Equals(expectedAddress))
            {
                problems.Add($"sshd is listening on an unexpected address: {activeAddress}");
            }
        }

        var safe = problems.Count == 0;
        var sshdDisabled = false;
        if (!safe && options.DisableOnUnsafe)
        {
            sshdDisabled = await DisableSshdAsync();
        }

        var result = new SshGuardResult
        {
            PolicyLabel = policy?.Label ?? "UNCONFIGURED",
            ExposureMode = policy?.ExposureMode ?? "private-only",
            AccessTokenRequired = policy?.AccessTokenRequired ?? false,
            ExpectedListenAddress = expected,
            ConfiguredListenAddresses = configured,
            ActiveListenAddresses = active,
            Safe = safe,
            SshdDisabled = sshdDisabled,
            Problems = problems,
            LogPath = logPath
        };

        WriteLog(result, logPath);
        return result;
    }

    private static async Task<bool> DisableSshdAsync()
    {
        try
        {
            await ProcessRunner.RunAsync("sc.exe", new[] { "config", "sshd", "start=", "demand" }, throwOnFailure: false);
            await ProcessRunner.RunAsync("sc.exe", new[] { "stop", "sshd" }, throwOnFailure: false);
            return true;
        }
        catch
        {
            return false;
        }
    }

    private static string GetDefaultLogPath()
    {
        var toolsDir = AppContext.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        var parent = Directory.GetParent(toolsDir)?.FullName ?? toolsDir;
        return Path.Combine(parent, "logs", "sshd-guard.log");
    }

    private static void WriteLog(SshGuardResult result, string path)
    {
        var directory = Path.GetDirectoryName(path);
        if (!string.IsNullOrWhiteSpace(directory))
        {
            Directory.CreateDirectory(directory);
        }

        var line = string.Join(
            " | ",
            new[]
            {
                result.Timestamp,
                $"label={result.PolicyLabel}",
                $"mode={result.ExposureMode}",
                $"safe={result.Safe}",
                $"disabled={result.SshdDisabled}",
                $"expected={result.ExpectedListenAddress}",
                $"configured={string.Join(",", result.ConfiguredListenAddresses)}",
                $"active={string.Join(",", result.ActiveListenAddresses)}",
                $"problems={string.Join("; ", result.Problems)}"
            });

        File.AppendAllText(path, line + Environment.NewLine, new UTF8Encoding(false));
    }
}
