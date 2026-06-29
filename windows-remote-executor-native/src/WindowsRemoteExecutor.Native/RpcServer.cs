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
        "host.guard",
        "host.repair",
        "host.tasks",
        "host.policy",
        "process.run",
        "process.capture",
        "process.spawn",
        "script.run",
        "script.capture",
        "python.run",
        "wsl.run",
        "wsl.capture",
        "wsl.script",
        "wsl.script.capture",
        "wsl.resident",
        "file.writeText",
        "file.readText",
        "file.mkdir",
        "file.deleteTree",
        "file.copy",
        "everything.search"
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
        var decodedPayload = decoded?.Payload.Deserialize<ProcessPayload>(RpcJson.Options);
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
                "host.guard" => await HostGuardAsync(request, startedAt),
                "host.repair" => await HostRepairAsync(request, startedAt),
                "host.tasks" => await HostTasksAsync(request, startedAt),
                "host.policy" => HostPolicy(request, startedAt),
                "process.run" => await ProcessCaptureAsync(request, startedAt, evidence: "process.run"),
                "process.capture" => await ProcessCaptureAsync(request, startedAt, evidence: "process.capture"),
                "process.spawn" => ProcessSpawn(request, startedAt),
                "script.run" => await ScriptCaptureAsync(request, startedAt, evidence: "script.run"),
                "script.capture" => await ScriptCaptureAsync(request, startedAt, evidence: "script.capture"),
                "python.run" => await PythonRunAsync(request, startedAt),
                "wsl.run" => await WslProcessAsync(request, startedAt, evidence: "wsl.run"),
                "wsl.capture" => await WslProcessAsync(request, startedAt, evidence: "wsl.capture"),
                "wsl.script" => await WslScriptAsync(request, startedAt, evidence: "wsl.script"),
                "wsl.script.capture" => await WslScriptAsync(request, startedAt, evidence: "wsl.script.capture"),
                "wsl.resident" => await WslResidentAsync(request, startedAt),
                "file.writetext" => FileWriteText(request, startedAt),
                "file.readtext" => FileReadText(request, startedAt),
                "file.mkdir" => FileMkdir(request, startedAt),
                "file.deletetree" => FileDeleteTree(request, startedAt),
                "file.copy" => FileCopy(request, startedAt),
                "everything.search" => EverythingSearchRpc(request, startedAt),
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
        catch (FileNotFoundException ex)
        {
            return RpcResponse.Failure(id, startedAt, DateTimeOffset.UtcNow, "not-found", ex.Message, exitCode: 2);
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
            "host.guard" => "guard-sshd",
            "host.repair" => "repair-sshd",
            "host.tasks" => "powershell-b64",
            "host.policy" => "probe",
            "process.run" => "run-b64",
            "process.capture" => "capture-b64",
            "process.spawn" => "spawn-b64",
            "script.run" => "exec-file-b64",
            "script.capture" => "exec-file-capture-b64",
            "python.run" => "python-b64",
            "wsl.run" => "wsl-b64",
            "wsl.capture" => "wsl-capture-b64",
            "wsl.script" => "wsl-script-b64",
            "wsl.script.capture" => "wsl-script-capture-b64",
            "wsl.resident" => "wsl-resident-b64",
            "file.writetext" => "copy-file-b64",
            "file.readtext" => "copy-file-b64",
            "file.mkdir" => "mkdir-b64",
            "file.deletetree" => "delete-tree-b64",
            "file.copy" => "copy-file-b64",
            "everything.search" => "everything-b64",
            _ => "probe"
        };
    }

    private static string[] NativeArgsForPolicy(RpcRequest request, string nativeCommand)
    {
        return nativeCommand switch
        {
            "run-b64" or "capture-b64" => ProcessArgsForPolicy(request.Payload),
            "spawn-b64" => SpawnArgsForPolicy(request.Payload),
            _ => Array.Empty<string>()
        };
    }

    private static string[] ProcessArgsForPolicy(JsonElement payload)
    {
        var process = DeserializePayload<ProcessPayload>(payload);
        return new[] { "--file", Base64Args.Encode(process.File) };
    }

    private static string[] SpawnArgsForPolicy(JsonElement payload)
    {
        var process = DeserializePayload<ProcessPayload>(payload);
        return new[] { "--file", Base64Args.Encode(process.File) };
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
            evidence: new[] { "rpc-stdio v3-only" });
    }

    private static RpcResponse HostProbe(string id, DateTimeOffset startedAt)
    {
        var probe = ProbeCollector.Collect();
        var json = JsonSerializer.Serialize(probe, RpcJson.Options);
        var stdoutText = json + Environment.NewLine;
        var endedAt = DateTimeOffset.UtcNow;
        return RpcResponse.Success(
            id,
            startedAt,
            endedAt,
            stdoutText: stdoutText,
            stdoutBytes: Encoding.UTF8.GetBytes(stdoutText),
            data: probe,
            evidence: new[] { "ProbeCollector.Collect" });
    }

    private static async Task<RpcResponse> HostGuardAsync(RpcRequest request, DateTimeOffset startedAt)
    {
        var payload = DeserializePayload<HostGuardPayload>(request.Payload);
        var result = await SshExposureGuard.EvaluateAsync(new SshGuardOptions
        {
            ExpectedListenAddress = payload.ExpectedListenAddress,
            LogPath = payload.LogPath,
            DisableOnUnsafe = !payload.NoDisable
        });
        var stdoutText = JsonSerializer.Serialize(result, RpcJson.Options) + Environment.NewLine;
        return RpcResponse.Success(
            request.Id,
            startedAt,
            DateTimeOffset.UtcNow,
            exitCode: result.Safe ? 0 : 3,
            stdoutText: stdoutText,
            stdoutBytes: Encoding.UTF8.GetBytes(stdoutText),
            data: result,
            evidence: new[] { "host.guard" });
    }

    private static async Task<RpcResponse> HostRepairAsync(RpcRequest request, DateTimeOffset startedAt)
    {
        var payload = DeserializePayload<HostRepairPayload>(request.Payload);
        var result = await SshRepair.RepairAsync(new SshRepairOptions
        {
            ExpectedListenAddress = payload.ExpectedListenAddress,
            CodexRoot = string.IsNullOrWhiteSpace(payload.CodexRoot) ? DefaultCodexRoot() : payload.CodexRoot!,
            LogPath = payload.LogPath,
            ForceRewrite = payload.ForceRewrite
        });
        var ok = result.Validated && result.ServiceRunning;
        var stdoutText = JsonSerializer.Serialize(result, RpcJson.Options) + Environment.NewLine;
        return RpcResponse.Success(
            request.Id,
            startedAt,
            DateTimeOffset.UtcNow,
            exitCode: ok ? 0 : 1,
            stdoutText: stdoutText,
            stdoutBytes: Encoding.UTF8.GetBytes(stdoutText),
            data: result,
            evidence: new[] { "host.repair" });
    }

    private static async Task<RpcResponse> HostTasksAsync(RpcRequest request, DateTimeOffset startedAt)
    {
        var payload = DeserializePayload<HostTasksPayload>(request.Payload);
        var prefix = payload.Prefix;
        if (payload.TaskNames.Count == 0 && prefix is null)
        {
            prefix = "CodexRemote";
        }
        var script = BuildTasksPowerShell(payload.TaskNames, prefix ?? string.Empty);
        var result = await ProcessRunner.RunCaptureAsync(
            "powershell.exe",
            new[] { "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script },
            workingDirectory: null,
            OutputEncodingPreference.Utf8);
        object? data = TryParseJson(result.StdOut);
        return FromProcessResult(request.Id, startedAt, result, data ?? new { taskNames = payload.TaskNames, prefix }, "host.tasks");
    }

    private static RpcResponse HostPolicy(RpcRequest request, DateTimeOffset startedAt)
    {
        var payload = DeserializePayload<HostPolicyPayload>(request.Payload);
        if (payload.ExposureMode is not "private-only" and not "public-with-token")
        {
            throw new ArgumentException("payload.exposureMode must be private-only or public-with-token.");
        }
        if (payload.CommandMode is not "standard" and not "argv-only")
        {
            throw new ArgumentException("payload.commandMode must be standard or argv-only.");
        }
        if (payload.ExposureMode == "public-with-token" && string.IsNullOrWhiteSpace(payload.Token))
        {
            throw new ArgumentException("public-with-token policy requires payload.token.");
        }

        var expected = payload.ExpectedListenAddress ?? AccessPolicy.TryLoadDefault()?.ExpectedListenAddress ?? string.Empty;
        var policy = new AccessPolicy
        {
            ExpectedListenAddress = expected,
            ExposureMode = payload.ExposureMode,
            CommandMode = payload.CommandMode,
            Label = payload.Label ?? DefaultPolicyLabel(payload.ExposureMode, payload.Token),
            AccessTokenSha256 = string.IsNullOrWhiteSpace(payload.Token) ? null : AccessPolicy.HashToken(payload.Token!),
            UpdatedAt = DateTimeOffset.Now.ToString("o")
        };
        var path = AccessPolicy.GetDefaultPath();
        var json = JsonSerializer.Serialize(policy, RpcJson.Options);
        File.WriteAllText(path, json, new UTF8Encoding(false));
        return RpcResponse.Success(
            request.Id,
            startedAt,
            DateTimeOffset.UtcNow,
            data: new { path, policy },
            evidence: new[] { "host.policy" });
    }

    private static async Task<RpcResponse> ProcessCaptureAsync(RpcRequest request, DateTimeOffset startedAt, string evidence)
    {
        var payload = DeserializePayload<ProcessPayload>(request.Payload);
        PathPolicy.EnsureSafePathShape(payload.File, "payload.file");
        if (!string.IsNullOrWhiteSpace(payload.Cwd))
        {
            PathPolicy.EnsureSafePathShape(payload.Cwd, "payload.cwd");
        }

        var result = await ExecutionCommands.CaptureProcessResultAsync(new RunProcessOptions
        {
            FilePath = payload.File,
            WorkingDirectory = payload.Cwd,
            Arguments = payload.Args
        });
        return FromProcessResult(request.Id, startedAt, result, new
        {
            file = payload.File,
            cwd = payload.Cwd,
            args = payload.Args
        }, evidence);
    }

    private static RpcResponse ProcessSpawn(RpcRequest request, DateTimeOffset startedAt)
    {
        var payload = DeserializePayload<ProcessPayload>(request.Payload);
        PathPolicy.EnsureSafePathShape(payload.File, "payload.file");
        if (!string.IsNullOrWhiteSpace(payload.Cwd))
        {
            PathPolicy.EnsureSafePathShape(payload.Cwd, "payload.cwd");
        }
        if (!string.IsNullOrWhiteSpace(payload.Stdout))
        {
            PathPolicy.EnsureSafePathShape(payload.Stdout, "payload.stdout");
        }
        if (!string.IsNullOrWhiteSpace(payload.Stderr))
        {
            PathPolicy.EnsureSafePathShape(payload.Stderr, "payload.stderr");
        }

        var result = ExecutionCommands.SpawnProcess(new SpawnProcessOptions
        {
            FilePath = payload.File,
            WorkingDirectory = payload.Cwd,
            StdOutPath = payload.Stdout,
            StdErrPath = payload.Stderr,
            Arguments = payload.Args
        });
        var stdoutText = JsonSerializer.Serialize(result, RpcJson.Options) + Environment.NewLine;
        return RpcResponse.Success(
            request.Id,
            startedAt,
            DateTimeOffset.UtcNow,
            stdoutText: stdoutText,
            stdoutBytes: Encoding.UTF8.GetBytes(stdoutText),
            data: result,
            evidence: new[] { "process.spawn" });
    }

    private static async Task<RpcResponse> ScriptCaptureAsync(RpcRequest request, DateTimeOffset startedAt, string evidence)
    {
        var payload = DeserializePayload<ScriptPayload>(request.Payload);
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

            return FromProcessResult(request.Id, startedAt, result, new
            {
                kind,
                cwd = payload.Cwd
            }, evidence);
        }
        finally
        {
            TryDelete(tempPath);
        }
    }

    private static async Task<RpcResponse> PythonRunAsync(RpcRequest request, DateTimeOffset startedAt)
    {
        var payload = DeserializePayload<PythonRunPayload>(request.Payload);
        var result = await ExecutionCommands.CapturePythonResultAsync(new PythonScriptOptions
        {
            ScriptPath = payload.ScriptPath,
            WorkingDirectory = payload.Cwd,
            PythonPath = payload.Python,
            CondaEnv = payload.CondaEnv,
            CondaPrefix = payload.CondaPrefix,
            ScriptArguments = payload.Args
        });
        return FromProcessResult(request.Id, startedAt, result, new
        {
            scriptPath = payload.ScriptPath,
            cwd = payload.Cwd,
            python = payload.Python,
            condaEnv = payload.CondaEnv,
            condaPrefix = payload.CondaPrefix,
            args = payload.Args
        }, "python.run");
    }

    private static async Task<RpcResponse> WslProcessAsync(RpcRequest request, DateTimeOffset startedAt, string evidence)
    {
        var payload = DeserializePayload<WslProcessPayload>(request.Payload);
        var result = await ExecutionCommands.CaptureWslProcessResultAsync(new WslProcessOptions
        {
            FilePath = payload.File,
            WorkingDirectory = payload.Cwd,
            Distribution = payload.Distribution,
            User = payload.User,
            Arguments = payload.Args
        });
        return FromProcessResult(request.Id, startedAt, result, new
        {
            file = payload.File,
            cwd = payload.Cwd,
            distribution = payload.Distribution,
            user = payload.User,
            args = payload.Args
        }, evidence);
    }

    private static async Task<RpcResponse> WslScriptAsync(RpcRequest request, DateTimeOffset startedAt, string evidence)
    {
        var payload = DeserializePayload<WslScriptPayload>(request.Payload);
        var result = await ExecutionCommands.CaptureWslScriptResultForRpcAsync(new WslScriptOptions
        {
            ScriptBody = payload.Script,
            WorkingDirectory = payload.Cwd,
            Distribution = payload.Distribution,
            User = payload.User,
            ShellPath = string.IsNullOrWhiteSpace(payload.Shell) ? "/bin/bash" : payload.Shell!,
            ScriptArguments = payload.Args
        });
        return FromProcessResult(request.Id, startedAt, result, new
        {
            cwd = payload.Cwd,
            distribution = payload.Distribution,
            user = payload.User,
            shell = string.IsNullOrWhiteSpace(payload.Shell) ? "/bin/bash" : payload.Shell,
            args = payload.Args
        }, evidence);
    }

    private static async Task<RpcResponse> WslResidentAsync(RpcRequest request, DateTimeOffset startedAt)
    {
        var payload = DeserializePayload<WslResidentPayload>(request.Payload);
        var result = await ExecutionCommands.CaptureWslResidentScriptForRpcAsync(payload.Script, new WslResidentOptions
        {
            StagePath = payload.StagePath ?? string.Empty,
            LaunchPath = payload.LaunchPath,
            WorkingDirectory = payload.Cwd,
            Distribution = payload.Distribution,
            User = payload.User,
            ShellPath = string.IsNullOrWhiteSpace(payload.Shell) ? "/bin/bash" : payload.Shell!,
            PidFile = payload.PidFile,
            LogFile = payload.LogFile,
            Port = payload.Port,
            HealthUrl = payload.HealthUrl,
            ReadyTimeoutSeconds = payload.ReadyTimeoutSeconds ?? 20,
            SettleDelaySeconds = payload.SettleDelaySeconds ?? 2,
            PollIntervalMilliseconds = payload.PollIntervalMilliseconds ?? 500,
            DiagnosticLines = payload.DiagnosticLines ?? 20,
            ScriptArguments = payload.Args
        });
        return FromProcessResult(request.Id, startedAt, result, TryParseJson(result.StdOut) ?? new
        {
            stagePath = payload.StagePath,
            launchPath = payload.LaunchPath,
            cwd = payload.Cwd,
            distribution = payload.Distribution,
            user = payload.User,
            args = payload.Args
        }, "wsl.resident");
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
        return RpcResponse.Success(
            request.Id,
            startedAt,
            DateTimeOffset.UtcNow,
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
        return RpcResponse.Success(
            request.Id,
            startedAt,
            DateTimeOffset.UtcNow,
            stdoutText: decoded.Text,
            stdoutEncoding: decoded.EncodingLabel,
            stdoutBytes: bytes,
            data: proof,
            evidence: new[] { "file.readText", proof.Sha256 });
    }

    private static RpcResponse FileMkdir(RpcRequest request, DateTimeOffset startedAt)
    {
        var payload = DeserializePayload<FilePathPayload>(request.Payload);
        var path = PathPolicy.NormalizeWindowsPath(payload.Path, "payload.path");
        Directory.CreateDirectory(path);
        return RpcResponse.Success(
            request.Id,
            startedAt,
            DateTimeOffset.UtcNow,
            data: new { path },
            evidence: new[] { "file.mkdir" });
    }

    private static RpcResponse FileDeleteTree(RpcRequest request, DateTimeOffset startedAt)
    {
        var payload = DeserializePayload<FilePathPayload>(request.Payload);
        var path = PathPolicy.NormalizeWindowsPath(payload.Path, "payload.path");
        if (Directory.Exists(path))
        {
            Directory.Delete(path, recursive: true);
        }
        else if (File.Exists(path))
        {
            File.Delete(path);
        }
        return RpcResponse.Success(
            request.Id,
            startedAt,
            DateTimeOffset.UtcNow,
            data: new { path },
            evidence: new[] { "file.deleteTree" });
    }

    private static RpcResponse FileCopy(RpcRequest request, DateTimeOffset startedAt)
    {
        var payload = DeserializePayload<FileCopyPayload>(request.Payload);
        var source = PathPolicy.NormalizeWindowsPath(payload.Source, "payload.source");
        var destination = PathPolicy.NormalizeWindowsPath(payload.Destination, "payload.destination");
        var parent = Path.GetDirectoryName(destination);
        if (!string.IsNullOrWhiteSpace(parent))
        {
            Directory.CreateDirectory(parent);
        }
        File.Copy(source, destination, overwrite: true);
        var proof = BuildFileProof(destination);
        return RpcResponse.Success(
            request.Id,
            startedAt,
            DateTimeOffset.UtcNow,
            data: new { source, destination, proof },
            evidence: new[] { "file.copy", proof.Sha256 });
    }

    private static RpcResponse EverythingSearchRpc(RpcRequest request, DateTimeOffset startedAt)
    {
        var payload = DeserializePayload<EverythingSearchPayload>(request.Payload);
        var results = EverythingSearch.Search(new EverythingSearchOptions
        {
            Query = payload.Query,
            MaxResults = payload.Max is > 0 ? (uint)payload.Max.Value : 100
        });
        var stdoutText = string.Join(Environment.NewLine, results) + (results.Count > 0 ? Environment.NewLine : string.Empty);
        return RpcResponse.Success(
            request.Id,
            startedAt,
            DateTimeOffset.UtcNow,
            stdoutText: stdoutText,
            stdoutBytes: Encoding.UTF8.GetBytes(stdoutText),
            data: new { results },
            evidence: new[] { "everything.search" });
    }

    private static RpcResponse FromProcessResult(string id, DateTimeOffset startedAt, ProcessResult result, object? data, string evidence)
    {
        return RpcResponse.Success(
            id,
            startedAt,
            DateTimeOffset.UtcNow,
            exitCode: result.ExitCode,
            stdoutText: result.StdOut,
            stderrText: result.StdErr,
            stdoutEncoding: result.StdOutEncoding,
            stderrEncoding: result.StdErrEncoding,
            stdoutBytes: result.StdOutBytes,
            stderrBytes: result.StdErrBytes,
            data: data,
            evidence: new[] { evidence, "rpc-stdio" });
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

    private static object? TryParseJson(string value)
    {
        var trimmed = value.Trim();
        if (string.IsNullOrWhiteSpace(trimmed))
        {
            return null;
        }
        try
        {
            using var document = JsonDocument.Parse(trimmed);
            return JsonSerializer.Deserialize<JsonElement>(document.RootElement.GetRawText(), RpcJson.Options);
        }
        catch (JsonException)
        {
            return null;
        }
    }

    private static string BuildTasksPowerShell(IReadOnlyList<string> taskNames, string prefix)
    {
        var taskNamesJson = JsonSerializer.Serialize(taskNames, RpcJson.Options);
        var prefixJson = JsonSerializer.Serialize(prefix, RpcJson.Options);
        return $$"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$taskNames = @({{taskNamesJson}} | ConvertFrom-Json)
$prefix = {{prefixJson}}
$tasks = New-Object System.Collections.Generic.List[object]
foreach ($taskName in $taskNames) {
  try {
    $tasks.Add((Get-ScheduledTask -TaskName $taskName -ErrorAction Stop))
  } catch {
    $tasks.Add([PSCustomObject]@{
      TaskName = $taskName
      TaskPath = '\'
      Missing = $true
    })
  }
}
if (-not [string]::IsNullOrWhiteSpace($prefix)) {
  Get-ScheduledTask | Where-Object { $_.TaskName -like ("$prefix*") } | ForEach-Object {
    $tasks.Add($_)
  }
}
$results = foreach ($task in $tasks | Sort-Object TaskPath, TaskName -Unique) {
  if ($task.PSObject.Properties.Name -contains 'Missing') {
    [PSCustomObject]@{
      taskName = $task.TaskName
      taskPath = $task.TaskPath
      missing = $true
    }
    continue
  }

  $taskInfo = Get-ScheduledTaskInfo -TaskName $task.TaskName -TaskPath $task.TaskPath
  [PSCustomObject]@{
    taskName = $task.TaskName
    taskPath = $task.TaskPath
    state = [string]$task.State
    enabled = [bool]$task.Settings.Enabled
    lastRunTime = $taskInfo.LastRunTime
    nextRunTime = $taskInfo.NextRunTime
    lastTaskResult = $taskInfo.LastTaskResult
    actions = @($task.Actions | ForEach-Object {
      [PSCustomObject]@{
        execute = $_.Execute
        arguments = $_.Arguments
        workingDirectory = $_.WorkingDirectory
      }
    })
  }
}
$results | ConvertTo-Json -Compress -Depth 6
""";
    }

    private static string DefaultCodexRoot()
    {
        var baseDir = AppContext.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        return Directory.GetParent(baseDir)?.FullName ?? @"C:\CodexRemote";
    }

    private static string DefaultPolicyLabel(string mode, string? token)
    {
        if (mode == "public-with-token")
        {
            return "PUBLIC-WITH-TOKEN EXPLICIT";
        }
        return string.IsNullOrWhiteSpace(token) ? "PRIVATE-ONLY" : "PRIVATE-ONLY TOKEN-REQUIRED";
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
