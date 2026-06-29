using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace WindowsRemoteExecutor.Native;

internal static class RpcServer
{
    private static readonly IReadOnlyList<string> SupportedActions = new[]
    {
        "host.capabilities",
        "host.probe",
        "process.capture",
        "script.capture",
        "file.writeText",
        "file.readText"
    };

    public static async Task<int> RunStdioAsync()
    {
        var line = await Console.In.ReadLineAsync();
        if (string.IsNullOrWhiteSpace(line))
        {
            await WriteResponseAsync(RpcResponse.Failure(
                string.Empty,
                DateTimeOffset.UtcNow,
                DateTimeOffset.UtcNow,
                "protocol",
                "rpc-stdio requires one JSON request line on stdin.",
                exitCode: 2));
            return 2;
        }

        RpcRequest? request;
        try
        {
            request = JsonSerializer.Deserialize<RpcRequest>(line, RpcJson.Options);
        }
        catch (JsonException ex)
        {
            await WriteResponseAsync(RpcResponse.Failure(
                string.Empty,
                DateTimeOffset.UtcNow,
                DateTimeOffset.UtcNow,
                "protocol",
                $"Invalid rpc-stdio JSON request: {ex.Message}",
                exitCode: 2));
            return 2;
        }

        request ??= new RpcRequest();
        var response = await DispatchAsync(request);
        await WriteResponseAsync(response);
        return response.Ok ? 0 : response.ExitCode == 0 ? 1 : response.ExitCode;
    }

    public static string BuildSelfTestJson()
    {
        var hostileArgs = new[]
        {
            "plain",
            "space arg",
            "quote \" arg",
            "tick ` dollar $ paren $(x)",
            "percent %PATH% bang !VAR! amp & pipe | lt < gt >",
            "json {\"a\":[1,2]}",
            "regex ^(a|b)+$",
            "url https://example.test/a?b=1&c=two",
            "cjk 日本語 中文 한글",
            "line1\nline2"
        };

        var request = new
        {
            id = "selftest-process-capture",
            action = "process.capture",
            payload = new
            {
                file = "C:/Program Files/Test Tool/tool.exe",
                cwd = "D:/Work Dir",
                args = hostileArgs
            }
        };

        var json = JsonSerializer.Serialize(request, RpcJson.Options);
        var decoded = JsonSerializer.Deserialize<RpcRequest>(json, RpcJson.Options);
        var decodedPayload = decoded?.Payload.Deserialize<ProcessCapturePayload>(RpcJson.Options);
        var hostilePayloadPreserved = decoded?.Action == "process.capture" &&
                                      decodedPayload is not null &&
                                      decodedPayload.File == "C:/Program Files/Test Tool/tool.exe" &&
                                      decodedPayload.Cwd == "D:/Work Dir" &&
                                      decodedPayload.Args.SequenceEqual(hostileArgs);
        var driveRelativeRejected = false;
        try
        {
            PathPolicy.NormalizeWindowsPath("D:badpath.txt", "path");
        }
        catch (ArgumentException)
        {
            driveRelativeRejected = true;
        }

        return JsonSerializer.Serialize(new
        {
            ok = hostilePayloadPreserved && driveRelativeRejected,
            protocol = "wre-rpc-stdio",
            version = 3,
            actions = SupportedActions,
            hostilePayloadPreserved,
            driveRelativeRejected,
            sampleRequest = JsonSerializer.Deserialize<JsonElement>(json)
        }, RpcJson.Options);
    }

    private static async Task<RpcResponse> DispatchAsync(RpcRequest request)
    {
        var startedAt = DateTimeOffset.UtcNow;
        var id = request.Id ?? string.Empty;
        try
        {
            if (string.IsNullOrWhiteSpace(request.Action))
            {
                return RpcResponse.Failure(id, startedAt, DateTimeOffset.UtcNow, "protocol", "RPC request requires action.", exitCode: 2);
            }

            EnsureAllowed(request);

            return request.Action.Trim().ToLowerInvariant() switch
            {
                "host.capabilities" => Capabilities(id, startedAt),
                "host.probe" => HostProbe(id, startedAt),
                "process.capture" => await ProcessCaptureAsync(request, startedAt),
                "script.capture" => await ScriptCaptureAsync(request, startedAt),
                "file.writetext" => FileWriteText(request, startedAt),
                "file.readtext" => FileReadText(request, startedAt),
                _ => RpcResponse.Failure(id, startedAt, DateTimeOffset.UtcNow, "unsupported", $"Unsupported rpc-stdio action: {request.Action}", exitCode: 2)
            };
        }
        catch (UnauthorizedAccessException ex)
        {
            return RpcResponse.Failure(id, startedAt, DateTimeOffset.UtcNow, "auth", ex.Message, exitCode: 3);
        }
        catch (ArgumentException ex)
        {
            return RpcResponse.Failure(id, startedAt, DateTimeOffset.UtcNow, ClassifyArgumentError(ex.Message), ex.Message, exitCode: 2);
        }
        catch (Exception ex)
        {
            return RpcResponse.Failure(id, startedAt, DateTimeOffset.UtcNow, "remote-exception", ex.ToString(), exitCode: 1);
        }
    }

    private static void EnsureAllowed(RpcRequest request)
    {
        var nativeCommand = NativeCommandForAction(request.Action);
        var args = NativeArgsForPolicy(request, nativeCommand);
        ExecutorAccessControl.EnsureCommandAllowed(nativeCommand, request.AccessToken, args);
    }

    private static string NativeCommandForAction(string action)
    {
        return action.Trim().ToLowerInvariant() switch
        {
            "host.capabilities" => "probe",
            "host.probe" => "probe",
            "process.capture" => "capture-b64",
            "script.capture" => "exec-file-capture-b64",
            "file.writetext" => "copy-file-b64",
            "file.readtext" => "copy-file-b64",
            _ => "invoke-b64"
        };
    }

    private static string[] NativeArgsForPolicy(RpcRequest request, string nativeCommand)
    {
        if (nativeCommand != "capture-b64")
        {
            return Array.Empty<string>();
        }

        var payload = DeserializePayload<ProcessCapturePayload>(request.Payload);
        return new[] { "--file", Base64Args.Encode(payload.File) };
    }

    private static RpcResponse Capabilities(string id, DateTimeOffset startedAt)
    {
        var endedAt = DateTimeOffset.UtcNow;
        return RpcResponse.Success(
            id,
            startedAt,
            endedAt,
            data: new CapabilitiesPayload
            {
                Actions = SupportedActions
            },
            evidence: new[] { "rpc-stdio v3 single-shot" });
    }

    private static RpcResponse HostProbe(string id, DateTimeOffset startedAt)
    {
        var probe = ProbeCollector.Collect();
        var json = JsonSerializer.Serialize(probe, RpcJson.Options);
        var stdoutBytes = Encoding.UTF8.GetBytes(json + Environment.NewLine);
        var endedAt = DateTimeOffset.UtcNow;
        return RpcResponse.Success(
            id,
            startedAt,
            endedAt,
            stdoutText: json + Environment.NewLine,
            stdoutBytes: stdoutBytes,
            data: probe,
            evidence: new[] { "ProbeCollector.Collect" });
    }

    private static async Task<RpcResponse> ProcessCaptureAsync(RpcRequest request, DateTimeOffset startedAt)
    {
        var payload = DeserializePayload<ProcessCapturePayload>(request.Payload);
        PathPolicy.EnsureSafePathShape(payload.File, "payload.file");
        if (!string.IsNullOrWhiteSpace(payload.Cwd))
        {
            PathPolicy.EnsureSafePathShape(payload.Cwd, "payload.cwd");
        }

        var result = await ProcessRunner.RunCaptureAsync(
            payload.File,
            payload.Args.ToArray(),
            payload.Cwd,
            OutputEncodingPreference.Auto);
        return FromProcessResult(request.Id, startedAt, result, data: new
        {
            file = payload.File,
            cwd = payload.Cwd,
            args = payload.Args
        });
    }

    private static async Task<RpcResponse> ScriptCaptureAsync(RpcRequest request, DateTimeOffset startedAt)
    {
        var payload = DeserializePayload<ScriptCapturePayload>(request.Payload);
        var kind = NormalizeScriptKind(payload.Kind);
        if (!string.IsNullOrWhiteSpace(payload.Cwd))
        {
            PathPolicy.EnsureSafePathShape(payload.Cwd, "payload.cwd");
        }
        if (!string.IsNullOrWhiteSpace(payload.Exe))
        {
            PathPolicy.EnsureSafePathShape(payload.Exe, "payload.exe");
        }

        var suffix = kind == "powershell" ? "ps1" : "cmd";
        var tempPath = Path.Combine(Path.GetTempPath(), $"windows-remote-executor-rpc-{Guid.NewGuid():N}.{suffix}");
        try
        {
            var normalizedScript = payload.Script.Replace("\r\n", "\n");
            await File.WriteAllTextAsync(tempPath, normalizedScript, new UTF8Encoding(false));
            ProcessResult result;
            if (kind == "powershell")
            {
                var exe = string.IsNullOrWhiteSpace(payload.Exe) ? "powershell.exe" : payload.Exe!;
                result = await ProcessRunner.RunCaptureAsync(
                    exe,
                    new[] { "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", tempPath },
                    payload.Cwd,
                    OutputEncodingPreference.Auto);
            }
            else
            {
                result = await ProcessRunner.RunCaptureAsync(
                    "cmd.exe",
                    new[] { "/d", "/q", "/c", tempPath },
                    payload.Cwd,
                    OutputEncodingPreference.Auto);
            }

            return FromProcessResult(request.Id, startedAt, result, data: new
            {
                kind,
                cwd = payload.Cwd
            });
        }
        finally
        {
            TryDelete(tempPath);
        }
    }

    private static RpcResponse FileWriteText(RpcRequest request, DateTimeOffset startedAt)
    {
        var payload = DeserializePayload<FileWriteTextPayload>(request.Payload);
        var path = PathPolicy.NormalizeWindowsPath(payload.Path, "payload.path");
        var parent = Path.GetDirectoryName(path);
        if (!string.IsNullOrWhiteSpace(parent))
        {
            Directory.CreateDirectory(parent);
        }

        File.WriteAllText(path, payload.Text, new UTF8Encoding(false));
        var proof = BuildFileProof(path);
        var endedAt = DateTimeOffset.UtcNow;
        return RpcResponse.Success(
            request.Id,
            startedAt,
            endedAt,
            data: proof,
            evidence: new[] { "file.writeText", proof.Sha256 });
    }

    private static RpcResponse FileReadText(RpcRequest request, DateTimeOffset startedAt)
    {
        var payload = DeserializePayload<FileReadTextPayload>(request.Payload);
        var path = PathPolicy.NormalizeWindowsPath(payload.Path, "payload.path");
        var info = new FileInfo(path);
        if (!info.Exists)
        {
            throw new FileNotFoundException($"File not found: {path}", path);
        }

        if (payload.MaxBytes is > 0 && info.Length > payload.MaxBytes.Value)
        {
            throw new ArgumentException($"File exceeds maxBytes: {info.Length} > {payload.MaxBytes.Value}");
        }

        var bytes = File.ReadAllBytes(path);
        var decoded = OutputDecoding.Decode(bytes, OutputEncodingPreference.Auto);
        var proof = BuildFileProof(path);
        var endedAt = DateTimeOffset.UtcNow;
        return RpcResponse.Success(
            request.Id,
            startedAt,
            endedAt,
            stdoutText: decoded.Text,
            stdoutEncoding: decoded.EncodingLabel,
            stdoutBytes: bytes,
            data: proof,
            evidence: new[] { "file.readText", proof.Sha256 });
    }

    private static RpcResponse FromProcessResult(string id, DateTimeOffset startedAt, ProcessResult result, object? data = null)
    {
        var endedAt = DateTimeOffset.UtcNow;
        return RpcResponse.Success(
            id,
            startedAt,
            endedAt,
            exitCode: result.ExitCode,
            stdoutText: result.StdOut,
            stderrText: result.StdErr,
            stdoutEncoding: result.StdOutEncoding,
            stderrEncoding: result.StdErrEncoding,
            stdoutBytes: result.StdOutBytes,
            stderrBytes: result.StdErrBytes,
            data: data,
            evidence: new[] { "ProcessRunner.RunCaptureAsync" });
    }

    private static T DeserializePayload<T>(JsonElement payload)
    {
        if (payload.ValueKind is JsonValueKind.Undefined or JsonValueKind.Null)
        {
            throw new ArgumentException("payload is required.");
        }

        return payload.Deserialize<T>(RpcJson.Options)
               ?? throw new ArgumentException("payload is invalid.");
    }

    private static string NormalizeScriptKind(string? value)
    {
        var normalized = string.IsNullOrWhiteSpace(value) ? "powershell" : value.Trim().ToLowerInvariant();
        return normalized switch
        {
            "ps" or "powershell" => "powershell",
            "cmd" or "batch" => "cmd",
            _ => throw new ArgumentException($"Unsupported script kind: {value}. Use powershell or cmd.")
        };
    }

    private static FileProof BuildFileProof(string path)
    {
        var info = new FileInfo(path);
        using var stream = File.OpenRead(path);
        var hash = SHA256.HashData(stream);
        return new FileProof
        {
            Path = path,
            Bytes = info.Length,
            Sha256 = Convert.ToHexString(hash).ToLowerInvariant(),
            LastWriteTimeUtc = info.LastWriteTimeUtc.ToString("O")
        };
    }

    private static string ClassifyArgumentError(string message)
    {
        return message.Contains("drive-relative", StringComparison.OrdinalIgnoreCase)
            ? "path-shape"
            : "request";
    }

    private static void TryDelete(string path)
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
            // Best effort cleanup for transient rpc-stdio script files.
        }
    }

    private static async Task WriteResponseAsync(RpcResponse response)
    {
        var json = JsonSerializer.Serialize(response, RpcJson.Options);
        await Console.Out.WriteLineAsync(json);
        await Console.Out.FlushAsync();
    }
}
