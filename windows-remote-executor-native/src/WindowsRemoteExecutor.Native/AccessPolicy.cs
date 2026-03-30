using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace WindowsRemoteExecutor.Native;

internal sealed class AccessPolicy
{
    public string ExpectedListenAddress { get; init; } = string.Empty;
    public string ExposureMode { get; init; } = "private-only";
    public string Label { get; init; } = "PRIVATE-ONLY";
    public string? AccessTokenSha256 { get; init; }
    public string? UpdatedAt { get; init; }

    public bool AccessTokenRequired => !string.IsNullOrWhiteSpace(AccessTokenSha256);
    public bool AllowsPublicExposure => ExposureMode.Equals("public-with-token", StringComparison.OrdinalIgnoreCase);

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

    public static void EnsureCommandAllowed(string command, string? accessToken)
    {
        if (!CommandRequiresTokenCheck(command))
        {
            return;
        }

        var policy = AccessPolicy.TryLoadDefault();
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
            "everything-b64" => true,
            _ => false
        };
    }
}
