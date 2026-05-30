using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace WindowsRemoteExecutor.Native;

internal sealed class AccessPolicy
{
    public string ExpectedListenAddress { get; init; } = string.Empty;
    public string ExposureMode { get; init; } = "private-only";
    public string CommandMode { get; init; } = "standard";
    public string Label { get; init; } = "PRIVATE-ONLY";
    public string? AccessTokenSha256 { get; init; }
    public string? UpdatedAt { get; init; }

    public bool AccessTokenRequired => !string.IsNullOrWhiteSpace(AccessTokenSha256);
    public bool AllowsPublicExposure => ExposureMode.Equals("public-with-token", StringComparison.OrdinalIgnoreCase);
    public bool EnforcesArgvOnly => CommandMode.Equals("argv-only", StringComparison.OrdinalIgnoreCase);

    public bool MatchesToken(string? providedToken)
    {
        if (!AccessTokenRequired)
        {
            return true;
        }

        if (string.IsNullOrWhiteSpace(providedToken))
        {
            return false;
        }

        var expected = Encoding.ASCII.GetBytes(AccessTokenSha256!);
        var actual = Encoding.ASCII.GetBytes(HashToken(providedToken));
        return CryptographicOperations.FixedTimeEquals(expected, actual);
    }

    public static AccessPolicy? TryLoadDefault()
    {
        var path = GetDefaultPath();
        if (!File.Exists(path))
        {
            return null;
        }

        var json = File.ReadAllText(path, Encoding.UTF8);
        return JsonSerializer.Deserialize<AccessPolicy>(json, JsonOptions);
    }

    public static string GetDefaultPath()
    {
        return Path.Combine(AppContext.BaseDirectory, "access-policy.json");
    }

    public static string HashToken(string token)
    {
        var bytes = SHA256.HashData(Encoding.UTF8.GetBytes(token));
        return Convert.ToHexString(bytes).ToLowerInvariant();
    }

    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true
    };
}

internal sealed class CommandSecurityContext
{
    public string? AccessToken { get; init; }
    public string[] RemainingArgs { get; init; } = Array.Empty<string>();
}

internal static class ExecutorAccessControl
{
    public static CommandSecurityContext Extract(string[] args)
    {
        string? accessToken = null;
        var remaining = new List<string>();

        for (var i = 0; i < args.Length; i++)
        {
            if (args[i] == "--access-token")
            {
                if (i + 1 >= args.Length)
                {
                    throw new ArgumentException("--access-token requires a base64 value.");
                }

                accessToken = Base64Args.Decode(args[++i]);
                continue;
            }

            remaining.Add(args[i]);
        }

        return new CommandSecurityContext
        {
            AccessToken = accessToken,
            RemainingArgs = remaining.ToArray()
        };
    }

    public static void EnsureCommandAllowed(string command, string? accessToken, string[] commandArgs)
    {
        var policy = AccessPolicy.TryLoadDefault();

        if (!CommandRequiresTokenCheck(command))
        {
            return;
        }

        if (policy is not null)
        {
            EnsureCommandModeAllowed(policy, command, commandArgs);
        }

        if (policy is null || !policy.AccessTokenRequired)
        {
            return;
        }

        if (!policy.MatchesToken(accessToken))
        {
            throw new UnauthorizedAccessException($"Access token required for command '{command}'.");
        }
    }

    private static bool CommandRequiresTokenCheck(string command)
    {
        return command switch
        {
            "probe" => true,
            "run-b64" => true,
            "capture-b64" => true,
            "python-b64" => true,
            "powershell-b64" => true,
            "exec-file-b64" => true,
            "exec-file-capture-b64" => true,
            "wsl-b64" => true,
            "wsl-capture-b64" => true,
            "wsl-script-b64" => true,
            "wsl-script-capture-b64" => true,
            "wsl-resident-b64" => true,
            "everything-b64" => true,
            _ => false
        };
    }

    private static void EnsureCommandModeAllowed(AccessPolicy policy, string command, string[] commandArgs)
    {
        if (!policy.EnforcesArgvOnly)
        {
            return;
        }

        switch (command)
        {
            case "run-b64":
            case "capture-b64":
                EnsureProgramAllowedForArgvOnly(command, commandArgs);
                return;

            case "probe":
            case "guard-sshd":
            case "repair-sshd":
            case "exec-file-b64":
            case "exec-file-capture-b64":
            case "everything-b64":
                return;

            case "python-b64":
            case "powershell-b64":
            case "wsl-b64":
            case "wsl-capture-b64":
            case "wsl-script-b64":
            case "wsl-script-capture-b64":
            case "wsl-resident-b64":
                throw new UnauthorizedAccessException(
                    $"Command '{command}' is blocked by access-policy commandMode=argv-only. Use run-b64/capture-b64 with an allowed executable and explicit argv, or exec-file-b64 for staged script maintenance.");

            default:
                return;
        }
    }

    private static void EnsureProgramAllowedForArgvOnly(string command, string[] commandArgs)
    {
        var filePath = TryReadBase64Option(commandArgs, "--file");
        if (string.IsNullOrWhiteSpace(filePath))
        {
            throw new ArgumentException("--file is required.");
        }

        var executableName = Path.GetFileName(filePath.Replace('\\', Path.DirectorySeparatorChar).Replace('/', Path.DirectorySeparatorChar));
        if (string.IsNullOrWhiteSpace(executableName))
        {
            executableName = filePath;
        }

        if (IsBlockedInterpreterOrShell(executableName))
        {
            throw new UnauthorizedAccessException(
                $"Program '{filePath}' is blocked by access-policy commandMode=argv-only for '{command}'. Shell, PowerShell, Python, and WSL interpreters are not allowed through run/capture.");
        }
    }

    private static string? TryReadBase64Option(string[] args, string option)
    {
        for (var i = 0; i < args.Length; i++)
        {
            if (args[i] != option)
            {
                continue;
            }

            if (i + 1 >= args.Length)
            {
                throw new ArgumentException($"{option} requires a base64 value.");
            }

            return Base64Args.Decode(args[i + 1]);
        }

        return null;
    }

    private static bool IsBlockedInterpreterOrShell(string executableName)
    {
        var normalized = executableName.Trim().ToLowerInvariant();
        return normalized switch
        {
            "cmd" or "cmd.exe" => true,
            "powershell" or "powershell.exe" => true,
            "pwsh" or "pwsh.exe" => true,
            "py" or "py.exe" => true,
            "python" or "python.exe" => true,
            "python3" or "python3.exe" => true,
            "wsl" or "wsl.exe" => true,
            "bash" or "bash.exe" => true,
            "sh" or "sh.exe" => true,
            _ => false
        };
    }
}
