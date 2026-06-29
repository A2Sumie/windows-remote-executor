using System.Text.Json;
using System.Text.Json.Serialization;

namespace WindowsRemoteExecutor.Native;

internal sealed class RpcRequest
{
    public string Id { get; init; } = string.Empty;
    public string Action { get; init; } = string.Empty;
    public int? TimeoutSeconds { get; init; }
    public int? CaptureLimitBytes { get; init; }
    public string? AccessToken { get; init; }
    public JsonElement Payload { get; init; }
}

internal sealed class RpcResponse
{
    public string Id { get; init; } = string.Empty;
    public bool Ok { get; init; }
    public int ExitCode { get; init; }
    public string ErrorClass { get; init; } = string.Empty;
    public string StdoutText { get; init; } = string.Empty;
    public string StderrText { get; init; } = string.Empty;
    public string StdoutEncoding { get; init; } = "utf-8";
    public string StderrEncoding { get; init; } = "utf-8";
    public string StdoutBase64 { get; init; } = string.Empty;
    public string StderrBase64 { get; init; } = string.Empty;
    public int StdoutBytes { get; init; }
    public int StderrBytes { get; init; }
    public string StartedAt { get; init; } = string.Empty;
    public string EndedAt { get; init; } = string.Empty;
    public long DurationMs { get; init; }
    public object? Data { get; init; }
    public IReadOnlyList<string> Evidence { get; init; } = Array.Empty<string>();

    public static RpcResponse Success(
        string id,
        DateTimeOffset startedAt,
        DateTimeOffset endedAt,
        int exitCode = 0,
        string stdoutText = "",
        string stderrText = "",
        string stdoutEncoding = "utf-8",
        string stderrEncoding = "utf-8",
        byte[]? stdoutBytes = null,
        byte[]? stderrBytes = null,
        object? data = null,
        IReadOnlyList<string>? evidence = null)
    {
        stdoutBytes ??= Array.Empty<byte>();
        stderrBytes ??= Array.Empty<byte>();
        return new RpcResponse
        {
            Id = id,
            Ok = exitCode == 0,
            ExitCode = exitCode,
            ErrorClass = exitCode == 0 ? string.Empty : "remote-process",
            StdoutText = stdoutText,
            StderrText = stderrText,
            StdoutEncoding = stdoutEncoding,
            StderrEncoding = stderrEncoding,
            StdoutBase64 = Convert.ToBase64String(stdoutBytes),
            StderrBase64 = Convert.ToBase64String(stderrBytes),
            StdoutBytes = stdoutBytes.Length,
            StderrBytes = stderrBytes.Length,
            StartedAt = startedAt.ToString("O"),
            EndedAt = endedAt.ToString("O"),
            DurationMs = (long)(endedAt - startedAt).TotalMilliseconds,
            Data = data,
            Evidence = evidence ?? Array.Empty<string>()
        };
    }

    public static RpcResponse Failure(
        string id,
        DateTimeOffset startedAt,
        DateTimeOffset endedAt,
        string errorClass,
        string message,
        int exitCode = 1,
        IReadOnlyList<string>? evidence = null)
    {
        var stderrBytes = System.Text.Encoding.UTF8.GetBytes(message);
        return new RpcResponse
        {
            Id = id,
            Ok = false,
            ExitCode = exitCode,
            ErrorClass = errorClass,
            StderrText = message,
            StderrBase64 = Convert.ToBase64String(stderrBytes),
            StderrBytes = stderrBytes.Length,
            StartedAt = startedAt.ToString("O"),
            EndedAt = endedAt.ToString("O"),
            DurationMs = (long)(endedAt - startedAt).TotalMilliseconds,
            Evidence = evidence ?? Array.Empty<string>()
        };
    }
}

internal sealed class ProcessCapturePayload
{
    public string File { get; init; } = string.Empty;
    public string? Cwd { get; init; }
    public IReadOnlyList<string> Args { get; init; } = Array.Empty<string>();
}

internal sealed class ScriptCapturePayload
{
    public string Kind { get; init; } = "powershell";
    public string Script { get; init; } = string.Empty;
    public string? Cwd { get; init; }
    public string? Exe { get; init; }
}

internal sealed class FileWriteTextPayload
{
    public string Path { get; init; } = string.Empty;
    public string Text { get; init; } = string.Empty;
}

internal sealed class FileReadTextPayload
{
    public string Path { get; init; } = string.Empty;
    public int? MaxBytes { get; init; }
}

internal sealed class FileProof
{
    public string Path { get; init; } = string.Empty;
    public long Bytes { get; init; }
    public string Sha256 { get; init; } = string.Empty;
    public string LastWriteTimeUtc { get; init; } = string.Empty;
}

internal sealed class CapabilitiesPayload
{
    public string Protocol { get; init; } = "wre-rpc-stdio";
    public int Version { get; init; } = 3;
    public IReadOnlyList<string> Actions { get; init; } = Array.Empty<string>();
}

internal static class RpcJson
{
    public static readonly JsonSerializerOptions Options = new()
    {
        PropertyNameCaseInsensitive = true,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull
    };
}
