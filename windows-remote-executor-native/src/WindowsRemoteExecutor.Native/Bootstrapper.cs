using System.Diagnostics;
using System.Security.Principal;
using System.Text;
using Microsoft.Win32;

namespace WindowsRemoteExecutor.Native;

internal sealed class BootstrapOptions
{
    public string TargetUser { get; set; } = Environment.UserName;
    public string ListenAddress { get; set; } = NetworkSafety.FindRecommendedListenAddress() ?? "127.0.0.1";
    public string CodexRoot { get; set; } = @"C:\CodexRemote";
    public bool SetPowerShellDefaultShell { get; set; }
    public bool ClearDefaultShell { get; set; }
    public bool InstallTailscale { get; set; }
    public string? AuthorizedKey { get; set; }
    public string? PublicKeyFile { get; set; }

    public string ResolveAuthorizedKey()
    {
        if (!string.IsNullOrWhiteSpace(AuthorizedKey))
        {
            return AuthorizedKey.Trim();
        }

        if (!string.IsNullOrWhiteSpace(PublicKeyFile))
        {
            if (!File.Exists(PublicKeyFile))
            {
                throw new FileNotFoundException($"Public key file not found: {PublicKeyFile}");
            }

            return File.ReadAllText(PublicKeyFile).Trim();
        }

        throw new InvalidOperationException("Provide --authorized-key or --public-key-file when bootstrapping SSH access.");
    }

    public static BootstrapOptions FromArgs(string[] args)
    {
        var options = new BootstrapOptions();

        for (var i = 0; i < args.Length; i++)
        {
            var arg = args[i];
            switch (arg)
            {
                case "--authorized-key":
                    options.AuthorizedKey = ReadValue(args, ref i, arg);
                    break;
                case "--public-key-file":
                    options.PublicKeyFile = ReadValue(args, ref i, arg);
                    break;
                case "--user":
                    options.TargetUser = ReadValue(args, ref i, arg);
                    break;
                case "--listen-address":
                    options.ListenAddress = ReadValue(args, ref i, arg);
                    break;
                case "--codex-root":
                    options.CodexRoot = ReadValue(args, ref i, arg);
                    break;
                case "--set-powershell-default-shell":
                    options.SetPowerShellDefaultShell = true;
                    break;
                case "--clear-default-shell":
                    options.ClearDefaultShell = true;
                    break;
                case "--install-tailscale":
                    options.InstallTailscale = true;
                    break;
                default:
                    throw new ArgumentException($"Unknown option: {arg}");
            }
        }

        return options;
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
}

internal sealed class BootstrapResult
{
    public string Hostname { get; init; } = Environment.MachineName;
    public string TargetUser { get; init; } = string.Empty;
    public string ListenAddress { get; init; } = string.Empty;
    public string CodexRoot { get; init; } = string.Empty;
    public string OpenSshCapabilityState { get; init; } = string.Empty;
    public ProbeServiceState? Sshd { get; init; }
    public ProbeServiceState? Tailscale { get; init; }
    public string? DefaultShell { get; init; }
    public string StartupConsoleScript { get; init; } = string.Empty;
    public string StartupConsoleLauncher { get; init; } = string.Empty;
    public string StartupConsoleTaskName { get; init; } = string.Empty;
    public string SshConfigPath { get; init; } = @"C:\ProgramData\ssh\sshd_config";
}

internal static class Bootstrapper
{
    private const string OpenSshCapability = "OpenSSH.Server~~~~0.0.1.0";
    private const string OpenSshFirewallRule = "Codex OpenSSH Server (Tailscale)";
    private const string StartupConsoleTaskName = "CodexRemote Console";
    private static readonly string OpenSshConfigPath = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),
        "ssh",
        "sshd_config");

    public static async Task<BootstrapResult> RunBootstrapAsync(BootstrapOptions options)
    {
        EnsureWindows();
        EnsureAdmin();

        var key = options.ResolveAuthorizedKey();
        var normalizedTargetUser = NormalizeTargetUser(options.TargetUser);

        WriteStep("Ensuring OpenSSH Server");
        await EnsureOpenSshServerAsync();

        WriteStep("Ensuring OpenSSH host keys");
        await EnsureOpenSshHostKeysAsync();

        WriteStep("Applying SSH listen address");
        SetSshListenAddress(options.ListenAddress);

        WriteStep("Preparing CodexRemote directories");
        EnsureCodexLayout(options.CodexRoot);

        WriteStep("Installing startup console");
        var startupConsole = await EnsureStartupConsoleAsync(options.CodexRoot, normalizedTargetUser, options.ListenAddress);

        WriteStep("Installing authorized keys");
        EnsureAuthorizedKeys(normalizedTargetUser, key);

        if (options.SetPowerShellDefaultShell)
        {
            WriteStep("Setting PowerShell as the default OpenSSH shell");
            SetDefaultShell();
        }
        else if (options.ClearDefaultShell)
        {
            WriteStep("Clearing custom OpenSSH default shell");
            ClearDefaultShell();
        }

        if (options.InstallTailscale)
        {
            WriteStep("Ensuring Tailscale is installed");
            await EnsureTailscaleInstalledAsync();
        }

        WriteStep("Configuring firewall and service startup");
        await EnsureScopedFirewallRuleAsync(options.ListenAddress);
        await ConfigureServiceStartupAsync();

        WriteStep("Validating sshd configuration");
        await RunRequiredAsync(Path.Combine(Environment.SystemDirectory, "OpenSSH", "sshd.exe"), "-t");

        WriteStep("Starting sshd");
        await StartSshdAsync();

        var probe = ProbeCollector.Collect();
        return new BootstrapResult
        {
            TargetUser = normalizedTargetUser,
            ListenAddress = options.ListenAddress,
            CodexRoot = options.CodexRoot,
            OpenSshCapabilityState = await GetOpenSshCapabilityStateAsync(),
            Sshd = probe.Ssh.Services.TryGetValue("sshd", out var sshd) ? sshd : null,
            Tailscale = probe.Ssh.Services.TryGetValue("Tailscale", out var tailscale) ? tailscale : null,
            DefaultShell = ReadDefaultShell(),
            StartupConsoleScript = startupConsole.ScriptPath,
            StartupConsoleLauncher = startupConsole.LauncherPath,
            StartupConsoleTaskName = startupConsole.TaskName,
        };
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

    private static async Task EnsureOpenSshServerAsync()
    {
        var state = await GetOpenSshCapabilityStateAsync();
        if (!string.Equals(state, "Installed", StringComparison.OrdinalIgnoreCase))
        {
            await RunRequiredAsync("dism.exe", $"/Online /Add-Capability /CapabilityName:{OpenSshCapability} /Quiet /NoRestart /English");
        }
    }

    private static async Task<string> GetOpenSshCapabilityStateAsync()
    {
        var output = await RunRequiredAsync("dism.exe", $"/Online /Get-CapabilityInfo /CapabilityName:{OpenSshCapability} /English");
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

    private static async Task EnsureOpenSshHostKeysAsync()
    {
        var sshKeyGen = Path.Combine(Environment.SystemDirectory, "OpenSSH", "ssh-keygen.exe");
        if (File.Exists(sshKeyGen))
        {
            await RunRequiredAsync(sshKeyGen, "-A");
        }
    }

    private static void SetSshListenAddress(string address)
    {
        _ = System.Net.IPAddress.Parse(address);

        if (!File.Exists(OpenSshConfigPath))
        {
            throw new FileNotFoundException($"sshd_config not found: {OpenSshConfigPath}");
        }

        var config = File.ReadAllText(OpenSshConfigPath);
        var normalized = config.Replace("\r\n", "\n");
        var lines = normalized
            .Split('\n')
            .Where(line => !line.TrimStart().StartsWith("ListenAddress ", StringComparison.OrdinalIgnoreCase))
            .ToList();

        var insertIndex = lines.FindIndex(line => line.TrimStart().StartsWith("Match ", StringComparison.OrdinalIgnoreCase));
        if (insertIndex < 0)
        {
            lines.Add($"ListenAddress {address}");
        }
        else
        {
            lines.Insert(insertIndex, $"ListenAddress {address}");
            lines.Insert(insertIndex + 1, string.Empty);
        }

        var rewritten = string.Join("\r\n", lines).TrimEnd() + "\r\n";
        File.WriteAllText(OpenSshConfigPath, rewritten, System.Text.Encoding.ASCII);
    }

    private static void EnsureCodexLayout(string codexRoot)
    {
        Directory.CreateDirectory(codexRoot);
        Directory.CreateDirectory(Path.Combine(codexRoot, "tools"));
        Directory.CreateDirectory(Path.Combine(codexRoot, "inbox"));
        Directory.CreateDirectory(Path.Combine(codexRoot, "staging"));
        Directory.CreateDirectory(Path.Combine(codexRoot, "apps"));
        Directory.CreateDirectory(Path.Combine(codexRoot, "logs"));
    }

    private static async Task<StartupConsolePaths> EnsureStartupConsoleAsync(string codexRoot, string targetUser, string listenAddress)
    {
        var scriptPath = Path.Combine(codexRoot, "tools", "codex-startup-console.cmd");
        var launcherPath = Path.Combine(codexRoot, "tools", "CodexRemote Console.cmd");
        var legacyStartupPath = Path.Combine(GetStartupFolderPath(targetUser), "CodexRemote Console.cmd");

        Directory.CreateDirectory(Path.GetDirectoryName(scriptPath)!);
        Directory.CreateDirectory(Path.GetDirectoryName(launcherPath)!);

        File.WriteAllText(scriptPath, BuildStartupConsoleScript(codexRoot, listenAddress), Encoding.ASCII);
        File.WriteAllText(launcherPath, BuildStartupConsoleLauncher(StartupConsoleTaskName), Encoding.ASCII);
        TryDeleteFile(legacyStartupPath);
        await EnsureStartupConsoleTaskAsync(targetUser, scriptPath, StartupConsoleTaskName);
        await RunProcessAllowFailureAsync("schtasks.exe", new[] { "/Run", "/TN", StartupConsoleTaskName });

        return new StartupConsolePaths(scriptPath, launcherPath, StartupConsoleTaskName);
    }

    private static void EnsureAuthorizedKeys(string targetUser, string authorizedKey)
    {
        var userProfile = ResolveUserProfile(targetUser);
        var userAuthorizedKeys = Path.Combine(userProfile, ".ssh", "authorized_keys");
        AppendUniqueLine(userAuthorizedKeys, authorizedKey);
        RunBestEffortIcacls(userAuthorizedKeys, $"{targetUser}:F");

        var adminAuthorizedKeys = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),
            "ssh",
            "administrators_authorized_keys");
        AppendUniqueLine(adminAuthorizedKeys, authorizedKey);
        RunBestEffortIcacls(adminAuthorizedKeys, "Administrators:F", "SYSTEM:F");
    }

    private static string ResolveUserProfile(string targetUser)
    {
        var sid = new NTAccount(targetUser).Translate(typeof(SecurityIdentifier)).Value;
        using var searcher = new System.Management.ManagementObjectSearcher(
            "root\\cimv2",
            $"SELECT LocalPath FROM Win32_UserProfile WHERE SID = '{sid.Replace("'", "''")}'");
        using var results = searcher.Get();
        foreach (var item in results.Cast<System.Management.ManagementObject>())
        {
            var localPath = item["LocalPath"]?.ToString();
            if (!string.IsNullOrWhiteSpace(localPath))
            {
                return localPath;
            }
        }

        return Path.Combine(@"C:\Users", GetTargetUserLeaf(targetUser));
    }

    private static string GetStartupFolderPath(string targetUser)
    {
        return Path.Combine(
            ResolveUserProfile(targetUser),
            "AppData",
            "Roaming",
            "Microsoft",
            "Windows",
            "Start Menu",
            "Programs",
            "Startup");
    }

    private static string BuildStartupConsoleScript(string codexRoot, string listenAddress)
    {
        var nativeExePath = ToWindowsCmdPath(Path.Combine(codexRoot, "tools", "WindowsRemoteExecutor.Native.exe"));
        var repairLogPath = ToWindowsCmdPath(Path.Combine(codexRoot, "logs", "sshd-repair.log"));
        var rootPath = ToWindowsCmdPath(codexRoot);
        var lines = new[]
        {
            "@echo off",
            "title CodexRemote Console",
            "echo [%DATE% %TIME%] CodexRemote startup console",
            "echo Host: %COMPUTERNAME%",
            "for /f \"delims=\" %%I in ('whoami') do echo User: %%I",
            $"echo Listen: {listenAddress}:22",
            $"echo Root: {rootPath}",
            "echo.",
            "set \"CODEX_SSHD_ATTEMPTS=6\"",
            "set \"CODEX_REPAIR_ATTEMPTED=0\"",
            $"set \"CODEX_NATIVE_EXE={nativeExePath}\"",
            $"set \"CODEX_SSH_REPAIR_LOG={repairLogPath}\"",
            "if exist \"%SystemRoot%\\System32\\OpenSSH\\sshd.exe\" (",
            "  \"%SystemRoot%\\System32\\OpenSSH\\sshd.exe\" -t >nul 2>nul",
            "  if errorlevel 1 (",
            "    echo sshd validation failed, attempting automatic repair...",
            "    call :repair_sshd",
            "  )",
            ")",
            "echo.",
            "sc.exe query Tailscale >nul 2>nul",
            "if not errorlevel 1 (",
            "  sc.exe query Tailscale | find \"RUNNING\" >nul",
            "  if errorlevel 1 (",
            "    echo Starting Tailscale...",
            "    sc.exe start Tailscale >nul 2>nul",
            "    ping -n 4 127.0.0.1 >nul",
            "  )",
            ")",
            "sc.exe query sshd >nul 2>nul",
            "if not errorlevel 1 (",
            "  for /l %%A in (1,1,%CODEX_SSHD_ATTEMPTS%) do (",
            "    sc.exe query sshd | find \"RUNNING\" >nul",
            "    if not errorlevel 1 goto :sshd_started",
            "    echo Starting sshd (attempt %%A/%CODEX_SSHD_ATTEMPTS%)...",
            "    sc.exe start sshd >nul 2>nul",
            "    ping -n 4 127.0.0.1 >nul",
            "  )",
            ")",
            "sc.exe query sshd | find \"RUNNING\" >nul",
            "if errorlevel 1 (",
            "  echo sshd is still not running, attempting automatic repair...",
            "  call :repair_sshd",
            ")",
            ":sshd_started",
            "echo.",
            "echo Service status:",
            "for %%S in (Tailscale sshd) do (",
            "  sc.exe query %%S >nul 2>nul",
            "  if not errorlevel 1 (",
                "    echo ---",
            "    sc.exe query %%S",
            "  )",
            ")",
            "echo.",
            "echo Listener check:",
            $"netstat -ano | findstr /R /C:\"{listenAddress}:22 .*LISTENING\"",
            "echo.",
            "echo This window stays open for local recovery commands.",
            "prompt CodexRemote $P$G",
            "goto :eof",
            string.Empty,
            ":repair_sshd",
            "if \"%CODEX_REPAIR_ATTEMPTED%\"==\"1\" goto :eof",
            "set \"CODEX_REPAIR_ATTEMPTED=1\"",
            "if not exist \"%CODEX_NATIVE_EXE%\" (",
            "  echo WARNING: %CODEX_NATIVE_EXE% not found; automatic repair unavailable.",
            "  goto :eof",
            ")",
            "\"%CODEX_NATIVE_EXE%\" repair-sshd --expected-listen-address " + listenAddress + " --log-path \"%CODEX_SSH_REPAIR_LOG%\"",
            string.Empty
        };

        return string.Join("\r\n", lines);
    }

    private static async Task EnsureStartupConsoleTaskAsync(string targetUser, string scriptPath, string taskName)
    {
        var command = BuildStartupConsoleTaskCommand(scriptPath);
        await RunProcessAllowFailureAsync("schtasks.exe", new[] { "/Delete", "/TN", taskName, "/F" });
        await RunRequiredAsync(
            "schtasks.exe",
            new[]
            {
                "/Create",
                "/TN",
                taskName,
                "/SC",
                "ONLOGON",
                "/RU",
                targetUser,
                "/RL",
                "HIGHEST",
                "/IT",
                "/TR",
                command,
                "/F"
            });
    }

    private static string BuildStartupConsoleTaskCommand(string scriptPath)
    {
        var cmdPath = Environment.GetEnvironmentVariable("ComSpec");
        if (string.IsNullOrWhiteSpace(cmdPath))
        {
            cmdPath = Path.Combine(Environment.SystemDirectory, "cmd.exe");
        }

        return $"\"{cmdPath}\" /k \"{scriptPath}\"";
    }

    private static string BuildStartupConsoleLauncher(string taskName)
    {
        var lines = new[]
        {
            "@echo off",
            $"schtasks.exe /Run /TN \"{taskName}\" >nul 2>nul",
            "if errorlevel 1 (",
            $"  echo Scheduled task \"{taskName}\" was not found or could not be started.",
            "  exit /b 1",
            ")",
            string.Empty
        };

        return string.Join("\r\n", lines);
    }

    private static void TryDeleteFile(string path)
    {
        try
        {
            if (File.Exists(path))
            {
                File.Delete(path);
            }
        }
        catch
        {
            // Best effort cleanup of the legacy Startup-folder launcher.
        }
    }

    private static void AppendUniqueLine(string filePath, string value)
    {
        var directory = Path.GetDirectoryName(filePath);
        if (!string.IsNullOrWhiteSpace(directory))
        {
            Directory.CreateDirectory(directory);
        }

        var existingLines = File.Exists(filePath)
            ? File.ReadAllLines(filePath).Select(line => line.Trim()).Where(line => line.Length > 0).ToHashSet(StringComparer.Ordinal)
            : new HashSet<string>(StringComparer.Ordinal);

        if (!existingLines.Contains(value))
        {
            using var writer = new StreamWriter(filePath, append: true, new System.Text.UTF8Encoding(false));
            writer.WriteLine(value);
        }
    }

    private static void RunBestEffortIcacls(string filePath, params string[] grants)
    {
        RunRequiredAsync("icacls.exe", $"\"{filePath}\" /inheritance:r").GetAwaiter().GetResult();
        foreach (var grant in grants)
        {
            RunRequiredAsync("icacls.exe", $"\"{filePath}\" /grant {grant}").GetAwaiter().GetResult();
        }
    }

    private static void SetDefaultShell()
    {
        using var key = Registry.LocalMachine.CreateSubKey(@"SOFTWARE\OpenSSH", writable: true);
        key?.SetValue("DefaultShell", Path.Combine(Environment.SystemDirectory, "WindowsPowerShell", "v1.0", "powershell.exe"), RegistryValueKind.String);
    }

    private static void ClearDefaultShell()
    {
        using var key = Registry.LocalMachine.CreateSubKey(@"SOFTWARE\OpenSSH", writable: true);
        key?.DeleteValue("DefaultShell", throwOnMissingValue: false);
        key?.DeleteValue("DefaultShellCommandOption", throwOnMissingValue: false);
        key?.DeleteValue("DefaultShellEscapeArguments", throwOnMissingValue: false);
    }

    private static async Task EnsureTailscaleInstalledAsync()
    {
        if (ProbeCollector.TryFindCommand("tailscale") is not null)
        {
            return;
        }

        if (ProbeCollector.TryFindCommand("winget") is null)
        {
            throw new InvalidOperationException("winget not found; cannot install Tailscale.");
        }

        await RunRequiredAsync("winget.exe", "install --id Tailscale.Tailscale --accept-package-agreements --accept-source-agreements --silent");
    }

    private static async Task EnsureScopedFirewallRuleAsync(string listenAddress)
    {
        await RunProcessAllowFailureAsync("netsh.exe", $"advfirewall firewall delete rule name=\"{OpenSshFirewallRule}\"");
        await RunRequiredAsync(
            "netsh.exe",
            $"advfirewall firewall add rule name=\"{OpenSshFirewallRule}\" dir=in action=allow protocol=TCP localport=22 localip={listenAddress}");
        await RunProcessAllowFailureAsync("netsh.exe", "advfirewall firewall delete rule name=\"OpenSSH Server (sshd)\"");
    }

    private static async Task ConfigureServiceStartupAsync()
    {
        if (ProbeCollector.TryGetService("Tailscale") is not null)
        {
            await RunRequiredAsync("cmd.exe", "/c sc.exe config Tailscale start= auto");
            await RunRequiredAsync("cmd.exe", "/c sc.exe config sshd depend= Tcpip/Tailscale");
        }
        else
        {
            await RunRequiredAsync("cmd.exe", "/c sc.exe config sshd depend= Tcpip");
        }

        await RunRequiredAsync("cmd.exe", "/c sc.exe config sshd start= auto");
        await RunRequiredAsync("reg.exe", @"add HKLM\SYSTEM\CurrentControlSet\Services\sshd /v DelayedAutostart /t REG_DWORD /d 0 /f");
    }

    private static async Task StartSshdAsync()
    {
        await RunProcessAllowFailureAsync("cmd.exe", "/c sc.exe stop sshd");
        await RunProcessAllowFailureAsync("cmd.exe", "/c sc.exe start sshd");

        for (var attempt = 0; attempt < 30; attempt++)
        {
            var sshd = ProbeCollector.TryGetService("sshd");
            if (sshd is not null && string.Equals(sshd.State, "Running", StringComparison.OrdinalIgnoreCase))
            {
                return;
            }

            await Task.Delay(500);
        }

        throw new InvalidOperationException("Failed to start sshd.");
    }

    private static string? ReadDefaultShell()
    {
        using var key = Registry.LocalMachine.OpenSubKey(@"SOFTWARE\OpenSSH", writable: false);
        return key?.GetValue("DefaultShell")?.ToString();
    }

    private static void WriteStep(string message)
    {
        Console.Error.WriteLine($"==> {message}");
    }

    private static string NormalizeTargetUser(string targetUser)
    {
        if (string.IsNullOrWhiteSpace(targetUser))
        {
            return $@"{Environment.MachineName}\{Environment.UserName}";
        }

        var candidates = targetUser.Contains('\\') || targetUser.Contains('@')
            ? new[] { targetUser }
            : new[] { $@"{Environment.MachineName}\{targetUser}", targetUser };

        foreach (var candidate in candidates)
        {
            try
            {
                var sid = (SecurityIdentifier)new NTAccount(candidate).Translate(typeof(SecurityIdentifier));
                var account = (NTAccount)sid.Translate(typeof(NTAccount));
                return account.Value;
            }
            catch
            {
                // Try the next candidate.
            }
        }

        return targetUser;
    }

    private static string GetTargetUserLeaf(string targetUser)
    {
        if (string.IsNullOrWhiteSpace(targetUser))
        {
            return Environment.UserName;
        }

        var separators = new[] { '\\', '/' };
        var leaf = targetUser.Split(separators, StringSplitOptions.RemoveEmptyEntries).LastOrDefault();
        return string.IsNullOrWhiteSpace(leaf) ? targetUser : leaf;
    }

    private static string ToWindowsCmdPath(string path)
    {
        return path.Replace('/', '\\');
    }

    private static async Task<ProcessResult> RunProcessAllowFailureAsync(string fileName, string arguments)
    {
        return await ProcessRunner.RunAsync(fileName, arguments, throwOnFailure: false);
    }

    private static async Task<ProcessResult> RunProcessAllowFailureAsync(string fileName, IReadOnlyList<string> arguments)
    {
        return await ProcessRunner.RunAsync(fileName, arguments, throwOnFailure: false);
    }

    private static async Task<ProcessResult> RunRequiredAsync(string fileName, string arguments)
    {
        return await ProcessRunner.RunAsync(fileName, arguments, throwOnFailure: true);
    }

    private static async Task<ProcessResult> RunRequiredAsync(string fileName, IReadOnlyList<string> arguments)
    {
        return await ProcessRunner.RunAsync(fileName, arguments, throwOnFailure: true);
    }
}

internal sealed record StartupConsolePaths(string ScriptPath, string LauncherPath, string TaskName);
