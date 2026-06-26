using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace WindowsRemoteExecutor.Native;

internal sealed class InvokeRequest
{
    public string Action { get; init; } = string.Empty;
    public string? File { get; init; }
    public string? Exe { get; init; }
    public string? Cwd { get; init; }
    public string? Kind { get; init; }
    public string? Script { get; init; }
    public string? ScriptPath { get; init; }
    public string? Source { get; init; }
    public string? Destination { get; init; }
    public string? Path { get; init; }
    public string? Stdout { get; init; }
    public string? Stderr { get; init; }
    public string? Python { get; init; }
    public string? CondaEnv { get; init; }
    public string? CondaPrefix { get; init; }
    public string? Distribution { get; init; }
    public string? User { get; init; }
    public string? Shell { get; init; }
    public string? StagePath { get; init; }
    public string? LaunchPath { get; init; }
    public string? PidFile { get; init; }
    public string? LogFile { get; init; }
    public int? Port { get; init; }
    public string? HealthUrl { get; init; }
    public int? ReadyTimeoutSeconds { get; init; }
    public int? SettleDelaySeconds { get; init; }
    public int? PollIntervalMilliseconds { get; init; }
    public int? DiagnosticLines { get; init; }
    public string? ExpectedListenAddress { get; init; }
    public string? CodexRoot { get; init; }
    public string? LogPath { get; init; }
    public bool? NoDisable { get; init; }
    public bool? ForceRewrite { get; init; }
    public string? Query { get; init; }
    public int? Max { get; init; }
    public IReadOnlyList<string> Args { get; init; } = Array.Empty<string>();
}

internal sealed class InvokePlan
{
    public string NativeCommand { get; init; } = string.Empty;
    public IReadOnlyList<string> NativeArgs { get; init; } = Array.Empty<string>();
}

internal static class InvokeRequestDispatcher
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull
    };

    public static async Task<int> RunAsync(string[] args, string? accessToken)
    {
        if (args.Length != 1)
        {
            throw new ArgumentException("invoke-b64 requires exactly one base64url JSON envelope argument.");
        }

        var request = DecodeRequest(args[0]);
        var plan = BuildPlan(request);
        ExecutorAccessControl.EnsureCommandAllowed(plan.NativeCommand, accessToken, plan.NativeArgs.ToArray());
        return await DispatchPlanAsync(plan);
    }

    public static string BuildSelfTestJson()
    {
        var cases = new[]
        {
            new InvokeRequest
            {
                Action = "process.capture",
                File = "C:/Program Files/Test Tool/tool.exe",
                Cwd = "D:/Work Dir",
                Args = new[]
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
                }
            },
            new InvokeRequest
            {
                Action = "script.capture",
                Kind = "powershell",
                ScriptPath = "C:/CodexRemote/staging/payload with spaces.ps1",
                Cwd = "C:/CodexRemote/inbox"
            },
            new InvokeRequest
            {
                Action = "wsl.script",
                Script = "printf '%s\\n' \"$1\"",
                Shell = "/bin/bash",
                Cwd = "/home/sumie/work dir",
                Args = new[] { "a b", "quote \" and $HOME" }
            },
            new InvokeRequest
            {
                Action = "file.copy",
                Source = "C:/CodexRemote/staging/source name.txt",
                Destination = "D:/Target Dir/dest name.txt"
            }
        };

        var plans = cases.Select(request => new
        {
            request.Action,
            plan = BuildPlan(request)
        }).ToArray();

        return JsonSerializer.Serialize(new { ok = true, cases = plans }, JsonOptions);
    }

    private static InvokeRequest DecodeRequest(string encoded)
    {
        var json = Encoding.UTF8.GetString(DecodeBase64Url(encoded));
        return JsonSerializer.Deserialize<InvokeRequest>(json, JsonOptions)
               ?? throw new ArgumentException("invoke-b64 envelope is empty or invalid JSON.");
    }

    public static string EncodeRequestForTest(InvokeRequest request)
    {
        var json = JsonSerializer.Serialize(request, JsonOptions);
        return EncodeBase64Url(Encoding.UTF8.GetBytes(json));
    }

    private static byte[] DecodeBase64Url(string value)
    {
        var padded = value.Replace('-', '+').Replace('_', '/');
        padded += (padded.Length % 4) switch
        {
            0 => string.Empty,
            2 => "==",
            3 => "=",
            _ => throw new ArgumentException("Invalid base64url envelope length.")
        };
        return Convert.FromBase64String(padded);
    }

    private static string EncodeBase64Url(byte[] bytes)
    {
        return Convert.ToBase64String(bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_');
    }

    private static InvokePlan BuildPlan(InvokeRequest request)
    {
        if (string.IsNullOrWhiteSpace(request.Action))
        {
            throw new ArgumentException("invoke-b64 envelope requires action.");
        }

        return request.Action.Trim().ToLowerInvariant() switch
        {
            "probe" => Plan("probe", Array.Empty<string>()),
            "process.run" => BuildProcessPlan("run-b64", request),
            "process.capture" => BuildProcessPlan("capture-b64", request),
            "process.spawn" => BuildSpawnPlan(request),
            "script.run" => BuildScriptFilePlan("exec-file-b64", request),
            "script.capture" => BuildScriptFilePlan("exec-file-capture-b64", request),
            "python.run" => BuildPythonPlan(request),
            "powershell.run" => BuildPowerShellPlan(request),
            "wsl.run" => BuildWslProcessPlan("wsl-b64", request),
            "wsl.capture" => BuildWslProcessPlan("wsl-capture-b64", request),
            "wsl.script" => BuildWslScriptPlan("wsl-script-b64", request),
            "wsl.script.capture" => BuildWslScriptPlan("wsl-script-capture-b64", request),
            "wsl.resident" => BuildWslResidentPlan(request),
            "file.mkdir" => BuildPathPlan("mkdir-b64", request),
            "file.delete-tree" => BuildPathPlan("delete-tree-b64", request),
            "file.copy" => BuildCopyFilePlan(request),
            "tasks.query" => BuildUnsupportedPlan("tasks.query"),
            "policy.apply" => BuildUnsupportedPlan("policy.apply"),
            "guard.run" => BuildGuardPlan(request),
            "repair.run" => BuildRepairPlan(request),
            "everything.search" => BuildEverythingPlan(request),
            _ => throw new ArgumentException($"Unsupported invoke-b64 action: {request.Action}")
        };
    }

    private static InvokePlan BuildProcessPlan(string command, InvokeRequest request)
    {
        var args = new List<string> { "--file", EncodeRequired(request.File, "file") };
        AddB64Option(args, "--cwd", request.Cwd);
        foreach (var value in request.Args)
        {
            args.Add("--arg");
            args.Add(Base64Args.Encode(value));
        }

        return Plan(command, args);
    }

    private static InvokePlan BuildSpawnPlan(InvokeRequest request)
    {
        var args = BuildProcessPlan("spawn-b64", request).NativeArgs.ToList();
        AddB64Option(args, "--stdout", request.Stdout);
        AddB64Option(args, "--stderr", request.Stderr);
        return Plan("spawn-b64", args);
    }

    private static InvokePlan BuildScriptFilePlan(string command, InvokeRequest request)
    {
        var args = new List<string>
        {
            "--kind",
            EncodeRequired(request.Kind ?? "powershell", "kind"),
            "--file",
            EncodeRequired(request.ScriptPath ?? request.File, "scriptPath")
        };
        AddB64Option(args, "--cwd", request.Cwd);
        AddB64Option(args, "--exe", request.Exe);
        return Plan(command, args);
    }

    private static InvokePlan BuildPythonPlan(InvokeRequest request)
    {
        var args = new List<string> { "--script", EncodeRequired(request.ScriptPath ?? request.File, "scriptPath") };
        AddB64Option(args, "--cwd", request.Cwd);
        AddB64Option(args, "--python", request.Python);
        AddB64Option(args, "--conda-env", request.CondaEnv);
        AddB64Option(args, "--conda-prefix", request.CondaPrefix);
        foreach (var value in request.Args)
        {
            args.Add("--arg");
            args.Add(Base64Args.Encode(value));
        }
        return Plan("python-b64", args);
    }

    private static InvokePlan BuildPowerShellPlan(InvokeRequest request)
    {
        var args = new List<string> { "--script", EncodeRequired(request.Script, "script") };
        AddB64Option(args, "--cwd", request.Cwd);
        AddB64Option(args, "--exe", request.Exe);
        return Plan("powershell-b64", args);
    }

    private static InvokePlan BuildWslProcessPlan(string command, InvokeRequest request)
    {
        var args = new List<string> { "--file", EncodeRequired(request.File, "file") };
        AddB64Option(args, "--cwd", request.Cwd);
        AddB64Option(args, "--distribution", request.Distribution);
        AddB64Option(args, "--user", request.User);
        foreach (var value in request.Args)
        {
            args.Add("--arg");
            args.Add(Base64Args.Encode(value));
        }
        return Plan(command, args);
    }

    private static InvokePlan BuildWslScriptPlan(string command, InvokeRequest request)
    {
        var args = new List<string> { "--script", EncodeRequired(request.Script, "script") };
        AddB64Option(args, "--cwd", request.Cwd);
        AddB64Option(args, "--distribution", request.Distribution);
        AddB64Option(args, "--user", request.User);
        AddB64Option(args, "--shell", request.Shell);
        foreach (var value in request.Args)
        {
            args.Add("--arg");
            args.Add(Base64Args.Encode(value));
        }
        return Plan(command, args);
    }

    private static InvokePlan BuildWslResidentPlan(InvokeRequest request)
    {
        var args = new List<string> { "--stage-path", EncodeRequired(request.StagePath, "stagePath") };
        AddB64Option(args, "--launch-path", request.LaunchPath);
        AddB64Option(args, "--cwd", request.Cwd);
        AddB64Option(args, "--distribution", request.Distribution);
        AddB64Option(args, "--user", request.User);
        AddB64Option(args, "--shell", request.Shell);
        AddB64Option(args, "--pid-file", request.PidFile);
        AddB64Option(args, "--log-file", request.LogFile);
        AddB64Option(args, "--port", request.Port?.ToString(System.Globalization.CultureInfo.InvariantCulture));
        AddB64Option(args, "--health-url", request.HealthUrl);
        AddB64Option(args, "--ready-timeout-seconds", request.ReadyTimeoutSeconds?.ToString(System.Globalization.CultureInfo.InvariantCulture));
        AddB64Option(args, "--settle-delay-seconds", request.SettleDelaySeconds?.ToString(System.Globalization.CultureInfo.InvariantCulture));
        AddB64Option(args, "--poll-interval-ms", request.PollIntervalMilliseconds?.ToString(System.Globalization.CultureInfo.InvariantCulture));
        AddB64Option(args, "--diagnostic-lines", request.DiagnosticLines?.ToString(System.Globalization.CultureInfo.InvariantCulture));
        foreach (var value in request.Args)
        {
            args.Add("--arg");
            args.Add(Base64Args.Encode(value));
        }
        return Plan("wsl-resident-b64", args);
    }

    private static InvokePlan BuildPathPlan(string command, InvokeRequest request)
    {
        return Plan(command, new[] { "--path", EncodeRequired(request.Path, "path") });
    }

    private static InvokePlan BuildCopyFilePlan(InvokeRequest request)
    {
        return Plan("copy-file-b64", new[]
        {
            "--source",
            EncodeRequired(request.Source, "source"),
            "--destination",
            EncodeRequired(request.Destination, "destination")
        });
    }

    private static InvokePlan BuildGuardPlan(InvokeRequest request)
    {
        var args = new List<string>();
        AddPlainOption(args, "--expected-listen-address", request.ExpectedListenAddress);
        AddPlainOption(args, "--log-path", request.LogPath);
        if (request.NoDisable == true)
        {
            args.Add("--no-disable");
        }
        return Plan("guard-sshd", args);
    }

    private static InvokePlan BuildRepairPlan(InvokeRequest request)
    {
        var args = new List<string>();
        AddPlainOption(args, "--expected-listen-address", request.ExpectedListenAddress);
        AddPlainOption(args, "--codex-root", request.CodexRoot);
        AddPlainOption(args, "--log-path", request.LogPath);
        if (request.ForceRewrite == true)
        {
            args.Add("--force-rewrite");
        }
        return Plan("repair-sshd", args);
    }

    private static InvokePlan BuildEverythingPlan(InvokeRequest request)
    {
        var args = new List<string> { "--query", EncodeRequired(request.Query, "query") };
        if (request.Max.HasValue)
        {
            args.Add("--max");
            args.Add(request.Max.Value.ToString(System.Globalization.CultureInfo.InvariantCulture));
        }
        return Plan("everything-b64", args);
    }

    private static InvokePlan BuildUnsupportedPlan(string action)
    {
        throw new ArgumentException($"invoke-b64 action '{action}' is not native-only yet; use the local wrapper compatibility path.");
    }

    private static InvokePlan Plan(string command, IEnumerable<string> args)
    {
        return new InvokePlan
        {
            NativeCommand = command,
            NativeArgs = args.ToArray()
        };
    }

    private static void AddB64Option(List<string> args, string option, string? value)
    {
        if (string.IsNullOrEmpty(value))
        {
            return;
        }
        args.Add(option);
        args.Add(Base64Args.Encode(value));
    }

    private static void AddPlainOption(List<string> args, string option, string? value)
    {
        if (string.IsNullOrEmpty(value))
        {
            return;
        }
        args.Add(option);
        args.Add(value);
    }

    private static string EncodeRequired(string? value, string name)
    {
        if (string.IsNullOrEmpty(value))
        {
            throw new ArgumentException($"invoke-b64 envelope requires {name}.");
        }
        return Base64Args.Encode(value);
    }

    private static async Task<int> DispatchPlanAsync(InvokePlan plan)
    {
        return plan.NativeCommand switch
        {
            "run-b64" => await ExecutionCommands.RunCommandAsync(plan.NativeArgs.ToArray()),
            "capture-b64" => await ExecutionCommands.CaptureCommandAsync(plan.NativeArgs.ToArray()),
            "spawn-b64" => ExecutionCommands.SpawnCommand(plan.NativeArgs.ToArray()),
            "python-b64" => await ExecutionCommands.RunPythonAsync(plan.NativeArgs.ToArray()),
            "powershell-b64" => await ExecutionCommands.RunPowerShellAsync(plan.NativeArgs.ToArray()),
            "exec-file-b64" => await ExecutionCommands.RunExecFileAsync(plan.NativeArgs.ToArray()),
            "exec-file-capture-b64" => await ExecutionCommands.CaptureExecFileAsync(plan.NativeArgs.ToArray()),
            "wsl-b64" => await ExecutionCommands.RunWslAsync(plan.NativeArgs.ToArray()),
            "wsl-capture-b64" => await ExecutionCommands.CaptureWslAsync(plan.NativeArgs.ToArray()),
            "wsl-script-b64" => await ExecutionCommands.RunWslScriptAsync(plan.NativeArgs.ToArray()),
            "wsl-script-capture-b64" => await ExecutionCommands.CaptureWslScriptAsync(plan.NativeArgs.ToArray()),
            "wsl-resident-b64" => await ExecutionCommands.RunWslResidentAsync(plan.NativeArgs.ToArray()),
            "mkdir-b64" => FileCommands.EnsureDirectory(plan.NativeArgs.ToArray()),
            "delete-tree-b64" => FileCommands.DeleteTree(plan.NativeArgs.ToArray()),
            "copy-file-b64" => FileCommands.CopyFile(plan.NativeArgs.ToArray()),
            "guard-sshd" => await SshExposureGuard.RunCommandAsync(plan.NativeArgs.ToArray()),
            "repair-sshd" => await SshRepair.RunCommandAsync(plan.NativeArgs.ToArray()),
            "everything-b64" => EverythingSearch.SearchToStdout(plan.NativeArgs.ToArray()),
            "probe" => WriteProbe(),
            _ => throw new ArgumentException($"Unsupported native command from invoke plan: {plan.NativeCommand}")
        };
    }

    private static int WriteProbe()
    {
        var probe = ProbeCollector.Collect();
        Console.WriteLine(JsonSerializer.Serialize(probe, JsonOptions));
        return 0;
    }
}
