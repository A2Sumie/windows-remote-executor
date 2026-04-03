using System.Net;
using System.Security.Principal;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace WindowsRemoteExecutor.Native;

internal sealed class SshRepairOptions
{
    public string? ExpectedListenAddress { get; init; }
    public string CodexRoot { get; init; } = GetDefaultCodexRoot();
    public string? LogPath { get; init; }
    public bool ForceRewrite { get; init; }

    public static SshRepairOptions FromArgs(string[] args)
    {
        string? expectedListenAddress = null;
        string? codexRoot = null;
        string? logPath = null;
        var forceRewrite = false;

        for (var i = 0; i < args.Length; i++)
        {
            switch (args[i])
            {
                case "--expected-listen-address":
                    expectedListenAddress = ReadValue(args, ref i, "--expected-listen-address");
                    break;

                case "--codex-root":
                    codexRoot = ReadValue(args, ref i, "--codex-root");
                    break;

                case "--log-path":
                    logPath = ReadValue(args, ref i, "--log-path");
                    break;

                case "--force-rewrite":
                    forceRewrite = true;
                    break;

                default:
                    throw new ArgumentException($"Unknown repair option: {args[i]}");
            }
        }

        return new SshRepairOptions
        {
            ExpectedListenAddress = expectedListenAddress,
            CodexRoot = string.IsNullOrWhiteSpace(codexRoot) ? GetDefaultCodexRoot() : codexRoot,
            LogPath = logPath,
            ForceRewrite = forceRewrite
        };
    }

    private static string ReadValue(string[] args, ref int index, string option)
    {
        if (index + 1 >= args.Length)
        {
            throw new ArgumentException($"{option} requires a value.");
        }

        index++;
        return args[index];
    }

    private static string GetDefaultCodexRoot()
    {
        var baseDir = AppContext.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        return Directory.GetParent(baseDir)?.FullName ?? @"C:\CodexRemote";
    }
}

internal sealed class SshRepairResult
{
    public string Timestamp { get; init; } = DateTimeOffset.Now.ToString("o");
    public string Hostname { get; init; } = Environment.MachineName;
    public string CodexRoot { get; init; } = string.Empty;
    public string ExpectedListenAddress { get; init; } = string.Empty;
    public string SshConfigPath { get; init; } = string.Empty;
    public string? BackupPath { get; init; }
    public string LogPath { get; init; } = string.Empty;
    public bool RewroteConfig { get; init; }
    public bool Validated { get; init; }
    public bool ServiceRunning { get; init; }
    public string ValidationSummary { get; init; } = string.Empty;
    public IReadOnlyList<string> Actions { get; init; } = Array.Empty<string>();
    public ProbeServiceState? Sshd { get; init; }
    public ProbeServiceState? Tailscale { get; init; }
}

internal static class SshRepair
{
    private const string OpenSshCapability = "OpenSSH.Server~~~~0.0.1.0";
    private const string OpenSshFirewallRule = "Codex OpenSSH Server (Tailscale)";
    private static readonly string OpenSshConfigPath = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),
        "ssh",
        "sshd_config");
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        WriteIndented = true
    };

    public static async Task<int> RunCommandAsync(string[] args)
    {
        var result = await RepairAsync(SshRepairOptions.FromArgs(args));
        Console.WriteLine(JsonSerializer.Serialize(result, JsonOptions));
        return result.Validated && result.ServiceRunning ? 0 : 1;
    }

    public static async Task<SshRepairResult> RepairAsync(SshRepairOptions options)
    {
        EnsureWindows();
        EnsureAdmin();

        var codexRoot = options.CodexRoot;
        var logPath = options.LogPath ?? Path.Combine(codexRoot, "logs", "sshd-repair.log");
        var expectedListenAddress = ResolveExpectedListenAddress(options.ExpectedListenAddress);
        var actions = new List<string>();

        await EnsureOpenSshServerAsync(actions);
        await EnsureOpenSshHostKeysAsync(actions);

        var validationBefore = await ValidateSshdConfigAsync();
        var rewroteConfig = options.ForceRewrite || validationBefore.ExitCode != 0 || !ConfigMatchesExpected(expectedListenAddress);
        string? backupPath = null;

        if (rewroteConfig)
        {
            backupPath = BackupExistingConfig();
            WriteManagedSshConfig(expectedListenAddress);
            actions.Add("rewrite-managed-sshd-config");
        }

        await EnsureScopedFirewallRuleAsync(expectedListenAddress, actions);
        await ConfigureServiceStartupAsync(actions);

        var validationAfter = await ValidateSshdConfigAsync();
        if (validationAfter.ExitCode != 0)
        {
            throw new InvalidOperationException(
                "sshd.exe -t still failed after repair: " + BuildValidationSummary(validationAfter));
        }

        await StartSshdAsync(actions);

        var probe = ProbeCollector.Collect();
        var result = new SshRepairResult
        {
            CodexRoot = codexRoot,
            ExpectedListenAddress = expectedListenAddress,
            SshConfigPath = OpenSshConfigPath,
            BackupPath = backupPath,
            LogPath = logPath,
            RewroteConfig = rewroteConfig,
            Validated = true,
            ServiceRunning = probe.Ssh.Services.TryGetValue("sshd", out var sshd) &&
                string.Equals(sshd.State, "Running", StringComparison.OrdinalIgnoreCase),
            ValidationSummary = BuildValidationSummary(validationAfter),
            Actions = actions,
            Sshd = probe.Ssh.Services.TryGetValue("sshd", out var service) ? service : null,
            Tailscale = probe.Ssh.Services.TryGetValue("Tailscale", out var tailscale) ? tailscale : null
        };

        WriteLog(result, logPath);
        return result;
    }

    private static void EnsureWindows()
    {
        if (!OperatingSystem.IsWindows())
        {
            throw new PlatformNotSupportedException("This command only runs on Windows.");
        }
    }

    private static void EnsureAdmin()
    {
        using var identity = WindowsIdentity.GetCurrent();
        var principal = new WindowsPrincipal(identity);
        if (!principal.IsInRole(WindowsBuiltInRole.Administrator))
        {
            throw new InvalidOperationException("Run this executable from an elevated Administrator shell.");
        }
    }

    private static string ResolveExpectedListenAddress(string? expectedListenAddress)
    {
        var candidate = expectedListenAddress;
        if (string.IsNullOrWhiteSpace(candidate))
        {
            candidate = AccessPolicy.TryLoadDefault()?.ExpectedListenAddress;
        }

        if (string.IsNullOrWhiteSpace(candidate))
        {
            candidate = ProbeCollector.ReadConfiguredSshListenAddresses()
                .FirstOrDefault(value => IPAddress.TryParse(value, out _));
        }

        if (string.IsNullOrWhiteSpace(candidate))
        {
            candidate = NetworkSafety.FindRecommendedListenAddress() ?? "127.0.0.1";
        }

        _ = IPAddress.Parse(candidate);
        return candidate;
    }

    private static async Task EnsureOpenSshServerAsync(List<string> actions)
    {
        var state = await GetOpenSshCapabilityStateAsync();
        if (!string.Equals(state, "Installed", StringComparison.OrdinalIgnoreCase))
        {
            await ProcessRunner.RunAsync(
                "dism.exe",
                $"/Online /Add-Capability /CapabilityName:{OpenSshCapability} /Quiet /NoRestart /English",
                throwOnFailure: true);
            actions.Add("install-openssh-server");
        }
    }

    private static async Task<string> GetOpenSshCapabilityStateAsync()
    {
        var output = await ProcessRunner.RunAsync(
            "dism.exe",
            $"/Online /Get-CapabilityInfo /CapabilityName:{OpenSshCapability} /English",
            throwOnFailure: true);

        foreach (var rawLine in output.StdOut.Split(Environment.NewLine))
        {
            var line = rawLine.Trim();
            if (!line.StartsWith("State", StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }

            var parts = line.Split(':', 2);
            if (parts.Length == 2)
            {
                return parts[1].Trim();
            }
        }

        return "Unknown";
    }

    private static async Task EnsureOpenSshHostKeysAsync(List<string> actions)
    {
        var sshKeyGen = Path.Combine(Environment.SystemDirectory, "OpenSSH", "ssh-keygen.exe");
        if (!File.Exists(sshKeyGen))
        {
            return;
        }

        await ProcessRunner.RunAsync(sshKeyGen, "-A", throwOnFailure: true);
        actions.Add("ensure-host-keys");
    }

    private static bool ConfigMatchesExpected(string expectedListenAddress)
    {
        if (!File.Exists(OpenSshConfigPath))
        {
            return false;
        }

        var configuredAddresses = ProbeCollector.ReadConfiguredSshListenAddresses();
        if (configuredAddresses.Count != 1 ||
            !string.Equals(configuredAddresses[0], expectedListenAddress, StringComparison.OrdinalIgnoreCase))
        {
            return false;
        }

        var config = File.ReadAllText(OpenSshConfigPath, Encoding.ASCII);
        if (!Regex.IsMatch(config, @"(?im)^\s*AuthorizedKeysFile\s+\.ssh/authorized_keys\s*$"))
        {
            return false;
        }

        if (!Regex.IsMatch(config, @"(?im)^\s*Subsystem\s+sftp\s+sftp-server\.exe\s*$"))
        {
            return false;
        }

        return Regex.IsMatch(
            config,
            @"(?ims)^\s*Match\s+Group\s+administrators\s*$.*?^\s*AuthorizedKeysFile\s+__PROGRAMDATA__/ssh/administrators_authorized_keys\s*$");
    }

    private static string? BackupExistingConfig()
    {
        if (!File.Exists(OpenSshConfigPath))
        {
            return null;
        }

        var backupPath = OpenSshConfigPath + ".bak-" + DateTime.Now.ToString("yyyyMMdd-HHmmss");
        File.Copy(OpenSshConfigPath, backupPath, overwrite: true);
        return backupPath;
    }

    private static void WriteManagedSshConfig(string expectedListenAddress)
    {
        var directory = Path.GetDirectoryName(OpenSshConfigPath);
        if (!string.IsNullOrWhiteSpace(directory))
        {
            Directory.CreateDirectory(directory);
        }

        var lines = new[]
        {
            "# Managed by WindowsRemoteExecutor.Native repair-sshd",
            "Port 22",
            "PubkeyAuthentication yes",
            "PasswordAuthentication no",
            "AuthorizedKeysFile .ssh/authorized_keys",
            "HostKey __PROGRAMDATA__/ssh/ssh_host_rsa_key",
            "HostKey __PROGRAMDATA__/ssh/ssh_host_ecdsa_key",
            "HostKey __PROGRAMDATA__/ssh/ssh_host_ed25519_key",
            "Subsystem sftp sftp-server.exe",
            $"ListenAddress {expectedListenAddress}",
            string.Empty,
            "Match Group administrators",
            "    AuthorizedKeysFile __PROGRAMDATA__/ssh/administrators_authorized_keys",
            string.Empty
        };

        File.WriteAllText(OpenSshConfigPath, string.Join("\r\n", lines), Encoding.ASCII);
    }

    private static async Task EnsureScopedFirewallRuleAsync(string listenAddress, List<string> actions)
    {
        await ProcessRunner.RunAsync(
            "netsh.exe",
            $"advfirewall firewall delete rule name=\"{OpenSshFirewallRule}\"",
            throwOnFailure: false);
        await ProcessRunner.RunAsync(
            "netsh.exe",
            $"advfirewall firewall add rule name=\"{OpenSshFirewallRule}\" dir=in action=allow protocol=TCP localport=22 localip={listenAddress}",
            throwOnFailure: true);
        await ProcessRunner.RunAsync(
            "netsh.exe",
            "advfirewall firewall delete rule name=\"OpenSSH Server (sshd)\"",
            throwOnFailure: false);
        actions.Add("scope-firewall-rule");
    }

    private static async Task ConfigureServiceStartupAsync(List<string> actions)
    {
        if (ProbeCollector.TryGetService("Tailscale") is not null)
        {
            await ProcessRunner.RunAsync("cmd.exe", "/c sc.exe config Tailscale start= auto", throwOnFailure: true);
            await ProcessRunner.RunAsync("cmd.exe", "/c sc.exe config sshd depend= Tcpip/Tailscale", throwOnFailure: true);
            actions.Add("set-sshd-dependency-tailscale");
        }
        else
        {
            await ProcessRunner.RunAsync("cmd.exe", "/c sc.exe config sshd depend= Tcpip", throwOnFailure: true);
            actions.Add("set-sshd-dependency-tcpip");
        }

        await ProcessRunner.RunAsync("cmd.exe", "/c sc.exe config sshd start= auto", throwOnFailure: true);
        await ProcessRunner.RunAsync(
            "cmd.exe",
            "/c sc.exe failure sshd reset= 86400 actions= restart/5000/restart/15000/restart/30000",
            throwOnFailure: true);
        await ProcessRunner.RunAsync("cmd.exe", "/c sc.exe failureflag sshd 1", throwOnFailure: false);
        await ProcessRunner.RunAsync(
            "reg.exe",
            @"add HKLM\SYSTEM\CurrentControlSet\Services\sshd /v DelayedAutostart /t REG_DWORD /d 0 /f",
            throwOnFailure: true);
        actions.Add("set-sshd-startup-auto");
    }

    private static async Task<ProcessResult> ValidateSshdConfigAsync()
    {
        return await ProcessRunner.RunAsync(
            Path.Combine(Environment.SystemDirectory, "OpenSSH", "sshd.exe"),
            "-t",
            throwOnFailure: false);
    }

    private static async Task StartSshdAsync(List<string> actions)
    {
        await ProcessRunner.RunAsync("cmd.exe", "/c sc.exe stop sshd", throwOnFailure: false);
        await ProcessRunner.RunAsync("cmd.exe", "/c sc.exe start sshd", throwOnFailure: false);

        for (var attempt = 0; attempt < 30; attempt++)
        {
            var sshd = ProbeCollector.TryGetService("sshd");
            if (sshd is not null && string.Equals(sshd.State, "Running", StringComparison.OrdinalIgnoreCase))
            {
                actions.Add("start-sshd");
                return;
            }

            await Task.Delay(500);
        }

        throw new InvalidOperationException("Failed to start sshd after repair.");
    }

    private static string BuildValidationSummary(ProcessResult result)
    {
        if (result.ExitCode == 0)
        {
            return "ok";
        }

        var parts = new[] { result.StdErr, result.StdOut }
            .Select(value => value?.Trim())
            .Where(value => !string.IsNullOrWhiteSpace(value))
            .ToArray();

        if (parts.Length == 0)
        {
            return $"exit={result.ExitCode}";
        }

        return string.Join(" | ", parts);
    }

    private static void WriteLog(SshRepairResult result, string path)
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
                $"expected={result.ExpectedListenAddress}",
                $"validated={result.Validated}",
                $"serviceRunning={result.ServiceRunning}",
                $"rewroteConfig={result.RewroteConfig}",
                $"backup={result.BackupPath ?? string.Empty}",
                $"actions={string.Join(",", result.Actions)}",
                $"summary={result.ValidationSummary}"
            });

        File.AppendAllText(path, line + Environment.NewLine, new UTF8Encoding(false));
    }
}
