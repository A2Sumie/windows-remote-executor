using System.Text.Json;
using System.Text;

namespace WindowsRemoteExecutor.Native;

internal sealed class RunProcessOptions
{
    public string FilePath { get; init; } = string.Empty;
    public string? WorkingDirectory { get; init; }
    public IReadOnlyList<string> Arguments { get; init; } = Array.Empty<string>();

    public static RunProcessOptions FromBase64Args(string[] args)
    {
        var filePath = string.Empty;
        string? workingDirectory = null;
        var processArgs = new List<string>();

        for (var i = 0; i < args.Length; i++)
        {
            switch (args[i])
            {
                case "--file":
                    filePath = Base64Args.ReadValue(args, ref i, "--file");
                    break;
                case "--cwd":
                    workingDirectory = Base64Args.ReadValue(args, ref i, "--cwd");
                    break;
                case "--arg":
                    processArgs.Add(Base64Args.ReadValue(args, ref i, "--arg"));
                    break;
                default:
                    throw new ArgumentException($"Unknown run option: {args[i]}");
            }
        }

        if (string.IsNullOrWhiteSpace(filePath))
        {
            throw new ArgumentException("--file is required.");
        }

        return new RunProcessOptions
        {
            FilePath = filePath,
            WorkingDirectory = workingDirectory,
            Arguments = processArgs
        };
    }
}

internal sealed class PythonScriptOptions
{
    public string ScriptPath { get; init; } = string.Empty;
    public string? WorkingDirectory { get; init; }
    public string? PythonPath { get; init; }
    public string? CondaEnv { get; init; }
    public string? CondaPrefix { get; init; }
    public IReadOnlyList<string> ScriptArguments { get; init; } = Array.Empty<string>();

    public static PythonScriptOptions FromBase64Args(string[] args)
    {
        var scriptPath = string.Empty;
        string? workingDirectory = null;
        string? pythonPath = null;
        string? condaEnv = null;
        string? condaPrefix = null;
        var scriptArgs = new List<string>();

        for (var i = 0; i < args.Length; i++)
        {
            switch (args[i])
            {
                case "--script":
                    scriptPath = Base64Args.ReadValue(args, ref i, "--script");
                    break;
                case "--cwd":
                    workingDirectory = Base64Args.ReadValue(args, ref i, "--cwd");
                    break;
                case "--python":
                    pythonPath = Base64Args.ReadValue(args, ref i, "--python");
                    break;
                case "--conda-env":
                    condaEnv = Base64Args.ReadValue(args, ref i, "--conda-env");
                    break;
                case "--conda-prefix":
                    condaPrefix = Base64Args.ReadValue(args, ref i, "--conda-prefix");
                    break;
                case "--arg":
                    scriptArgs.Add(Base64Args.ReadValue(args, ref i, "--arg"));
                    break;
                default:
                    throw new ArgumentException($"Unknown python option: {args[i]}");
            }
        }

        if (string.IsNullOrWhiteSpace(scriptPath))
        {
            throw new ArgumentException("--script is required.");
        }

        if (!string.IsNullOrWhiteSpace(pythonPath) &&
            (!string.IsNullOrWhiteSpace(condaEnv) || !string.IsNullOrWhiteSpace(condaPrefix)))
        {
            throw new ArgumentException("--python cannot be combined with --conda-env or --conda-prefix.");
        }

        if (!string.IsNullOrWhiteSpace(condaEnv) && !string.IsNullOrWhiteSpace(condaPrefix))
        {
            throw new ArgumentException("--conda-env and --conda-prefix are mutually exclusive.");
        }

        return new PythonScriptOptions
        {
            ScriptPath = scriptPath,
            WorkingDirectory = workingDirectory,
            PythonPath = pythonPath,
            CondaEnv = condaEnv,
            CondaPrefix = condaPrefix,
            ScriptArguments = scriptArgs
        };
    }
}

internal sealed class PowerShellScriptOptions
{
    public string ScriptBody { get; init; } = string.Empty;
    public string? WorkingDirectory { get; init; }
    public string? PowerShellExecutable { get; init; }

    public static PowerShellScriptOptions FromBase64Args(string[] args)
    {
        var scriptBody = string.Empty;
        string? workingDirectory = null;
        string? powerShellExecutable = null;

        for (var i = 0; i < args.Length; i++)
        {
            switch (args[i])
            {
                case "--script":
                    scriptBody = Base64Args.ReadValue(args, ref i, "--script");
                    break;
                case "--cwd":
                    workingDirectory = Base64Args.ReadValue(args, ref i, "--cwd");
                    break;
                case "--exe":
                    powerShellExecutable = Base64Args.ReadValue(args, ref i, "--exe");
                    break;
                default:
                    throw new ArgumentException($"Unknown PowerShell option: {args[i]}");
            }
        }

        if (string.IsNullOrWhiteSpace(scriptBody))
        {
            throw new ArgumentException("--script is required.");
        }

        return new PowerShellScriptOptions
        {
            ScriptBody = scriptBody,
            WorkingDirectory = workingDirectory,
            PowerShellExecutable = powerShellExecutable
        };
    }
}

internal sealed class ExecScriptFileOptions
{
    public string Kind { get; init; } = "powershell";
    public string ScriptPath { get; init; } = string.Empty;
    public string? WorkingDirectory { get; init; }
    public string? PowerShellExecutable { get; init; }

    public static ExecScriptFileOptions FromBase64Args(string[] args)
    {
        var kind = "powershell";
        var scriptPath = string.Empty;
        string? workingDirectory = null;
        string? powerShellExecutable = null;

        for (var i = 0; i < args.Length; i++)
        {
            switch (args[i])
            {
                case "--kind":
                    kind = Base64Args.ReadValue(args, ref i, "--kind");
                    break;
                case "--file":
                case "--script-file":
                    scriptPath = Base64Args.ReadValue(args, ref i, args[i]);
                    break;
                case "--cwd":
                    workingDirectory = Base64Args.ReadValue(args, ref i, "--cwd");
                    break;
                case "--exe":
                    powerShellExecutable = Base64Args.ReadValue(args, ref i, "--exe");
                    break;
                default:
                    throw new ArgumentException($"Unknown exec-file option: {args[i]}");
            }
        }

        if (string.IsNullOrWhiteSpace(scriptPath))
        {
            throw new ArgumentException("--file is required.");
        }

        kind = NormalizeKind(kind);

        return new ExecScriptFileOptions
        {
            Kind = kind,
            ScriptPath = scriptPath,
            WorkingDirectory = workingDirectory,
            PowerShellExecutable = powerShellExecutable
        };
    }

    private static string NormalizeKind(string value)
    {
        var normalized = value.Trim().ToLowerInvariant();
        return normalized switch
        {
            "ps" or "powershell" => "powershell",
            "cmd" or "batch" => "cmd",
            _ => throw new ArgumentException("--kind must be powershell or cmd.")
        };
    }
}

internal sealed class WslProcessOptions
{
    public string FilePath { get; init; } = string.Empty;
    public string? WorkingDirectory { get; init; }
    public string? Distribution { get; init; }
    public string? User { get; init; }
    public IReadOnlyList<string> Arguments { get; init; } = Array.Empty<string>();

    public static WslProcessOptions FromBase64Args(string[] args)
    {
        var filePath = string.Empty;
        string? workingDirectory = null;
        string? distribution = null;
        string? user = null;
        var processArgs = new List<string>();

        for (var i = 0; i < args.Length; i++)
        {
            switch (args[i])
            {
                case "--file":
                    filePath = Base64Args.ReadValue(args, ref i, "--file");
                    break;
                case "--cwd":
                    workingDirectory = Base64Args.ReadValue(args, ref i, "--cwd");
                    break;
                case "--distribution":
                    distribution = Base64Args.ReadValue(args, ref i, "--distribution");
                    break;
                case "--user":
                    user = Base64Args.ReadValue(args, ref i, "--user");
                    break;
                case "--arg":
                    processArgs.Add(Base64Args.ReadValue(args, ref i, "--arg"));
                    break;
                default:
                    throw new ArgumentException($"Unknown WSL option: {args[i]}");
            }
        }

        if (string.IsNullOrWhiteSpace(filePath))
        {
            throw new ArgumentException("--file is required.");
        }

        return new WslProcessOptions
        {
            FilePath = filePath,
            WorkingDirectory = workingDirectory,
            Distribution = distribution,
            User = user,
            Arguments = processArgs
        };
    }
}

internal sealed class WslScriptOptions
{
    public string ScriptBody { get; init; } = string.Empty;
    public string? WorkingDirectory { get; init; }
    public string? Distribution { get; init; }
    public string? User { get; init; }
    public string ShellPath { get; init; } = "/bin/bash";
    public IReadOnlyList<string> ScriptArguments { get; init; } = Array.Empty<string>();

    public static WslScriptOptions FromBase64Args(string[] args)
    {
        var scriptBody = string.Empty;
        string? workingDirectory = null;
        string? distribution = null;
        string? user = null;
        var shellPath = "/bin/bash";
        var scriptArgs = new List<string>();

        for (var i = 0; i < args.Length; i++)
        {
            switch (args[i])
            {
                case "--script":
                    scriptBody = Base64Args.ReadValue(args, ref i, "--script");
                    break;
                case "--cwd":
                    workingDirectory = Base64Args.ReadValue(args, ref i, "--cwd");
                    break;
                case "--distribution":
                    distribution = Base64Args.ReadValue(args, ref i, "--distribution");
                    break;
                case "--user":
                    user = Base64Args.ReadValue(args, ref i, "--user");
                    break;
                case "--shell":
                    shellPath = Base64Args.ReadValue(args, ref i, "--shell");
                    break;
                case "--arg":
                    scriptArgs.Add(Base64Args.ReadValue(args, ref i, "--arg"));
                    break;
                default:
                    throw new ArgumentException($"Unknown WSL script option: {args[i]}");
            }
        }

        if (string.IsNullOrWhiteSpace(scriptBody))
        {
            throw new ArgumentException("--script is required.");
        }

        if (string.IsNullOrWhiteSpace(shellPath))
        {
            throw new ArgumentException("--shell cannot be empty.");
        }

        return new WslScriptOptions
        {
            ScriptBody = scriptBody,
            WorkingDirectory = workingDirectory,
            Distribution = distribution,
            User = user,
            ShellPath = shellPath,
            ScriptArguments = scriptArgs
        };
    }
}

internal sealed class WslResidentOptions
{
    public string StagePath { get; init; } = string.Empty;
    public string? LaunchPath { get; init; }
    public string? WorkingDirectory { get; init; }
    public string? Distribution { get; init; }
    public string? User { get; init; }
    public string ShellPath { get; init; } = "/bin/bash";
    public string? PidFile { get; init; }
    public string? LogFile { get; init; }
    public int? Port { get; init; }
    public string? HealthUrl { get; init; }
    public int ReadyTimeoutSeconds { get; init; } = 20;
    public int SettleDelaySeconds { get; init; } = 2;
    public int PollIntervalMilliseconds { get; init; } = 500;
    public int DiagnosticLines { get; init; } = 20;
    public IReadOnlyList<string> ScriptArguments { get; init; } = Array.Empty<string>();

    public static WslResidentOptions FromBase64Args(string[] args)
    {
        var stagePath = string.Empty;
        string? launchPath = null;
        string? workingDirectory = null;
        string? distribution = null;
        string? user = null;
        var shellPath = "/bin/bash";
        string? pidFile = null;
        string? logFile = null;
        int? port = null;
        string? healthUrl = null;
        var readyTimeoutSeconds = 20;
        var settleDelaySeconds = 2;
        var pollIntervalMilliseconds = 500;
        var diagnosticLines = 20;
        var scriptArgs = new List<string>();

        for (var i = 0; i < args.Length; i++)
        {
            switch (args[i])
            {
                case "--stage-path":
                    stagePath = Base64Args.ReadValue(args, ref i, "--stage-path");
                    break;
                case "--launch-path":
                    launchPath = Base64Args.ReadValue(args, ref i, "--launch-path");
                    break;
                case "--cwd":
                    workingDirectory = Base64Args.ReadValue(args, ref i, "--cwd");
                    break;
                case "--distribution":
                    distribution = Base64Args.ReadValue(args, ref i, "--distribution");
                    break;
                case "--user":
                    user = Base64Args.ReadValue(args, ref i, "--user");
                    break;
                case "--shell":
                    shellPath = Base64Args.ReadValue(args, ref i, "--shell");
                    break;
                case "--pid-file":
                    pidFile = Base64Args.ReadValue(args, ref i, "--pid-file");
                    break;
                case "--log-file":
                    logFile = Base64Args.ReadValue(args, ref i, "--log-file");
                    break;
                case "--port":
                    port = ParsePositiveInt(Base64Args.ReadValue(args, ref i, "--port"), "--port");
                    break;
                case "--health-url":
                    healthUrl = Base64Args.ReadValue(args, ref i, "--health-url");
                    break;
                case "--ready-timeout-seconds":
                    readyTimeoutSeconds = ParseNonNegativeInt(
                        Base64Args.ReadValue(args, ref i, "--ready-timeout-seconds"),
                        "--ready-timeout-seconds");
                    break;
                case "--settle-delay-seconds":
                    settleDelaySeconds = ParseNonNegativeInt(
                        Base64Args.ReadValue(args, ref i, "--settle-delay-seconds"),
                        "--settle-delay-seconds");
                    break;
                case "--poll-interval-ms":
                    pollIntervalMilliseconds = ParsePositiveInt(
                        Base64Args.ReadValue(args, ref i, "--poll-interval-ms"),
                        "--poll-interval-ms");
                    break;
                case "--diagnostic-lines":
                    diagnosticLines = ParsePositiveInt(
                        Base64Args.ReadValue(args, ref i, "--diagnostic-lines"),
                        "--diagnostic-lines");
                    break;
                case "--arg":
                    scriptArgs.Add(Base64Args.ReadValue(args, ref i, "--arg"));
                    break;
                default:
                    throw new ArgumentException($"Unknown WSL resident option: {args[i]}");
            }
        }

        if (string.IsNullOrWhiteSpace(stagePath))
        {
            throw new ArgumentException("--stage-path is required.");
        }

        if (string.IsNullOrWhiteSpace(shellPath))
        {
            throw new ArgumentException("--shell cannot be empty.");
        }

        return new WslResidentOptions
        {
            StagePath = stagePath,
            LaunchPath = launchPath,
            WorkingDirectory = workingDirectory,
            Distribution = distribution,
            User = user,
            ShellPath = shellPath,
            PidFile = pidFile,
            LogFile = logFile,
            Port = port,
            HealthUrl = healthUrl,
            ReadyTimeoutSeconds = readyTimeoutSeconds,
            SettleDelaySeconds = settleDelaySeconds,
            PollIntervalMilliseconds = pollIntervalMilliseconds,
            DiagnosticLines = diagnosticLines,
            ScriptArguments = scriptArgs
        };
    }

    private static int ParseNonNegativeInt(string value, string option)
    {
        if (!int.TryParse(value, out var parsed) || parsed < 0)
        {
            throw new ArgumentException($"{option} must be a non-negative integer.");
        }

        return parsed;
    }

    private static int ParsePositiveInt(string value, string option)
    {
        if (!int.TryParse(value, out var parsed) || parsed <= 0)
        {
            throw new ArgumentException($"{option} must be a positive integer.");
        }

        return parsed;
    }
}

internal static class ExecutionCommands
{
    public static async Task<int> RunCommandAsync(string[] args)
    {
        var options = RunProcessOptions.FromBase64Args(args);
        return await ProcessRunner.RunPassthroughAsync(
            options.FilePath,
            options.Arguments,
            options.WorkingDirectory,
            OutputEncodingPreference.Auto);
    }

    public static async Task<int> CaptureCommandAsync(string[] args)
    {
        var options = RunProcessOptions.FromBase64Args(args);
        var result = await ProcessRunner.RunCaptureAsync(
            options.FilePath,
            options.Arguments,
            options.WorkingDirectory,
            OutputEncodingPreference.Auto);

        var payload = new
        {
            exitCode = result.ExitCode,
            stdoutText = result.StdOut,
            stderrText = result.StdErr,
            stdoutEncoding = result.StdOutEncoding,
            stderrEncoding = result.StdErrEncoding,
            stdoutBase64 = Convert.ToBase64String(result.StdOutBytes),
            stderrBase64 = Convert.ToBase64String(result.StdErrBytes),
            stdoutBytes = result.StdOutBytes.Length,
            stderrBytes = result.StdErrBytes.Length
        };
        Console.WriteLine(JsonSerializer.Serialize(payload));
        return result.ExitCode;
    }

    public static async Task<int> RunPythonAsync(string[] args)
    {
        var options = PythonScriptOptions.FromBase64Args(args);
        var plan = ResolvePythonExecution(options);
        return await ProcessRunner.RunPassthroughAsync(
            plan.FilePath,
            plan.Arguments,
            plan.WorkingDirectory,
            OutputEncodingPreference.Utf8,
            new Dictionary<string, string?>
            {
                ["PYTHONUTF8"] = "1",
                ["PYTHONIOENCODING"] = "utf-8"
            });
    }

    public static async Task<int> RunPowerShellAsync(string[] args)
    {
        var options = PowerShellScriptOptions.FromBase64Args(args);
        return await RunPowerShellScriptAsync(
            options.ScriptBody,
            options.WorkingDirectory,
            options.PowerShellExecutable);
    }

    public static async Task<int> RunExecFileAsync(string[] args)
    {
        var options = ExecScriptFileOptions.FromBase64Args(args);
        return await RunExecFileInternalAsync(options);
    }

    public static async Task<int> CaptureExecFileAsync(string[] args)
    {
        var options = ExecScriptFileOptions.FromBase64Args(args);
        var result = await CaptureExecFileResultAsync(options);
        WriteCapturePayload(result);
        return result.ExitCode;
    }

    private static async Task<int> RunPowerShellScriptAsync(
        string scriptBody,
        string? workingDirectory,
        string? powerShellExecutable)
    {
        var executable = ResolvePowerShellExecutable(powerShellExecutable);
        return await ProcessRunner.RunPassthroughAsync(
            executable,
            BuildPowerShellEncodedArguments(scriptBody),
            workingDirectory,
            OutputEncodingPreference.Utf8);
    }

    private static async Task<ProcessResult> CapturePowerShellScriptAsync(
        string scriptBody,
        string? workingDirectory,
        string? powerShellExecutable)
    {
        var executable = ResolvePowerShellExecutable(powerShellExecutable);
        return await ProcessRunner.RunCaptureAsync(
            executable,
            BuildPowerShellEncodedArguments(scriptBody),
            workingDirectory,
            OutputEncodingPreference.Utf8);
    }

    private static IReadOnlyList<string> BuildPowerShellEncodedArguments(string scriptBody)
    {
        var wrappedScript = ComposePowerShellScript(scriptBody);
        var encodedCommand = Convert.ToBase64String(Encoding.Unicode.GetBytes(wrappedScript));
        return new[]
        {
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encodedCommand
        };
    }

    private static async Task<int> RunExecFileInternalAsync(ExecScriptFileOptions options)
    {
        return options.Kind switch
        {
            "powershell" => await RunPowerShellScriptAsync(
                ReadScriptFile(options.ScriptPath),
                options.WorkingDirectory,
                options.PowerShellExecutable),
            "cmd" => await RunCmdScriptFileAsync(options.ScriptPath, options.WorkingDirectory),
            _ => throw new ArgumentException($"Unsupported exec-file kind: {options.Kind}")
        };
    }

    private static async Task<ProcessResult> CaptureExecFileResultAsync(ExecScriptFileOptions options)
    {
        return options.Kind switch
        {
            "powershell" => await CapturePowerShellScriptAsync(
                ReadScriptFile(options.ScriptPath),
                options.WorkingDirectory,
                options.PowerShellExecutable),
            "cmd" => await CaptureCmdScriptFileAsync(options.ScriptPath, options.WorkingDirectory),
            _ => throw new ArgumentException($"Unsupported exec-file kind: {options.Kind}")
        };
    }

    private static async Task<int> RunCmdScriptFileAsync(string scriptPath, string? workingDirectory)
    {
        return await ProcessRunner.RunPassthroughAsync(
            ResolveCmdExecutable(),
            new[] { "/d", "/q", "/c", scriptPath },
            workingDirectory,
            OutputEncodingPreference.Auto);
    }

    private static async Task<ProcessResult> CaptureCmdScriptFileAsync(string scriptPath, string? workingDirectory)
    {
        return await ProcessRunner.RunCaptureAsync(
            ResolveCmdExecutable(),
            new[] { "/d", "/q", "/c", scriptPath },
            workingDirectory,
            OutputEncodingPreference.Auto);
    }

    public static async Task<int> RunWslAsync(string[] args)
    {
        var options = WslProcessOptions.FromBase64Args(args);
        return await RunWslProcessAsync(options);
    }

    public static async Task<int> CaptureWslAsync(string[] args)
    {
        var options = WslProcessOptions.FromBase64Args(args);
        return await CaptureWslProcessAsync(options);
    }

    public static async Task<int> RunWslScriptAsync(string[] args)
    {
        var options = WslScriptOptions.FromBase64Args(args);
        return await RunWslScriptInternalAsync(options);
    }

    public static async Task<int> CaptureWslScriptAsync(string[] args)
    {
        var options = WslScriptOptions.FromBase64Args(args);
        return await CaptureWslScriptInternalAsync(options);
    }

    public static async Task<int> RunWslResidentAsync(string[] args)
    {
        var options = WslResidentOptions.FromBase64Args(args);
        return await CaptureWslResidentAsync(options);
    }

    private static RunProcessOptions ResolvePythonExecution(PythonScriptOptions options)
    {
        if (!string.IsNullOrWhiteSpace(options.PythonPath))
        {
            return new RunProcessOptions
            {
                FilePath = options.PythonPath,
                WorkingDirectory = options.WorkingDirectory,
                Arguments = BuildPythonArguments(options.ScriptPath, options.ScriptArguments)
            };
        }

        if (!string.IsNullOrWhiteSpace(options.CondaEnv) || !string.IsNullOrWhiteSpace(options.CondaPrefix))
        {
            var condaExecutable = FindCondaExecutable();
            if (condaExecutable is null)
            {
                throw new InvalidOperationException("Conda executable not found. Pass --python explicitly or install a detectable conda.exe.");
            }

            var arguments = new List<string> { "run", "--no-capture-output" };
            if (!string.IsNullOrWhiteSpace(options.CondaEnv))
            {
                arguments.Add("-n");
                arguments.Add(options.CondaEnv);
            }
            else
            {
                arguments.Add("-p");
                arguments.Add(options.CondaPrefix!);
            }

            arguments.Add("python");
            arguments.AddRange(BuildPythonArguments(options.ScriptPath, options.ScriptArguments));

            return new RunProcessOptions
            {
                FilePath = condaExecutable,
                WorkingDirectory = options.WorkingDirectory,
                Arguments = arguments
            };
        }

        var defaultPython = FindPreferredPython();
        if (defaultPython is null)
        {
            throw new InvalidOperationException("No usable Python interpreter found. Pass --python or provide --conda-env/--conda-prefix.");
        }

        return new RunProcessOptions
        {
            FilePath = defaultPython.FilePath,
            WorkingDirectory = options.WorkingDirectory,
            Arguments = defaultPython.AdditionalArguments
                .Concat(BuildPythonArguments(options.ScriptPath, options.ScriptArguments))
                .ToArray()
        };
    }

    private static IReadOnlyList<string> BuildPythonArguments(string scriptPath, IReadOnlyList<string> scriptArguments)
    {
        var arguments = new List<string> { scriptPath };
        arguments.AddRange(scriptArguments);
        return arguments;
    }

    private static ResolvedExecutable? FindPreferredPython()
    {
        var pythonExe = ProbeCollector.TryFindCommand("python.exe");
        if (!string.IsNullOrWhiteSpace(pythonExe))
        {
            return new ResolvedExecutable(pythonExe, Array.Empty<string>());
        }

        var pyLauncher = ProbeCollector.TryFindCommand("py.exe");
        if (!string.IsNullOrWhiteSpace(pyLauncher))
        {
            return new ResolvedExecutable(pyLauncher, new[] { "-3" });
        }

        foreach (var root in GetCommonCondaRoots())
        {
            var pythonPath = Path.Combine(root, "python.exe");
            if (File.Exists(pythonPath))
            {
                return new ResolvedExecutable(pythonPath, Array.Empty<string>());
            }
        }

        return null;
    }

    private static string? FindCondaExecutable()
    {
        var fromEnv = Environment.GetEnvironmentVariable("CONDA_EXE");
        if (!string.IsNullOrWhiteSpace(fromEnv) && File.Exists(fromEnv) && Path.GetExtension(fromEnv).Equals(".exe", StringComparison.OrdinalIgnoreCase))
        {
            return fromEnv;
        }

        foreach (var name in new[] { "conda.exe", "conda" })
        {
            var command = ProbeCollector.TryFindCommand(name);
            if (string.IsNullOrWhiteSpace(command))
            {
                continue;
            }

            if (Path.GetExtension(command).Equals(".exe", StringComparison.OrdinalIgnoreCase))
            {
                return command;
            }

            var inferred = TryInferCondaExe(command);
            if (!string.IsNullOrWhiteSpace(inferred))
            {
                return inferred;
            }
        }

        foreach (var root in GetCommonCondaRoots())
        {
            var candidate = Path.Combine(root, "Scripts", "conda.exe");
            if (File.Exists(candidate))
            {
                return candidate;
            }
        }

        return null;
    }

    private static string? TryInferCondaExe(string discoveredPath)
    {
        var fullPath = Path.GetFullPath(discoveredPath);
        var fileName = Path.GetFileName(fullPath);
        if (!fileName.Equals("conda.bat", StringComparison.OrdinalIgnoreCase) &&
            !fileName.Equals("conda.cmd", StringComparison.OrdinalIgnoreCase))
        {
            return null;
        }

        var condabinDir = Path.GetDirectoryName(fullPath);
        var rootDir = condabinDir is null ? null : Directory.GetParent(condabinDir)?.FullName;
        if (string.IsNullOrWhiteSpace(rootDir))
        {
            return null;
        }

        var condaExe = Path.Combine(rootDir, "Scripts", "conda.exe");
        return File.Exists(condaExe) ? condaExe : null;
    }

    private static IEnumerable<string> GetCommonCondaRoots()
    {
        var roots = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var userProfile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        var localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);

        foreach (var root in new[]
        {
            Path.Combine(userProfile, "Anaconda3"),
            Path.Combine(userProfile, "Miniconda3"),
            Path.Combine(userProfile, "miniconda3"),
            Path.Combine(userProfile, "miniforge3"),
            Path.Combine(userProfile, "mambaforge"),
            Path.Combine(localAppData, "anaconda3"),
            Path.Combine(localAppData, "miniconda3")
        })
        {
            if (Directory.Exists(root))
            {
                roots.Add(root);
            }
        }

        return roots;
    }

    private static string ResolvePowerShellExecutable(string? configuredExecutable)
    {
        if (!string.IsNullOrWhiteSpace(configuredExecutable))
        {
            var discovered = ProbeCollector.TryFindCommand(configuredExecutable);
            if (!string.IsNullOrWhiteSpace(discovered))
            {
                return discovered;
            }

            return configuredExecutable;
        }

        foreach (var candidate in new[] { "powershell.exe", "powershell", "pwsh.exe", "pwsh" })
        {
            var discovered = ProbeCollector.TryFindCommand(candidate);
            if (!string.IsNullOrWhiteSpace(discovered))
            {
                return discovered;
            }
        }

        var inboxPowerShell = Path.Combine(Environment.SystemDirectory, "WindowsPowerShell", "v1.0", "powershell.exe");
        if (File.Exists(inboxPowerShell))
        {
            return inboxPowerShell;
        }

        throw new InvalidOperationException("No usable PowerShell executable found. Pass --exe explicitly or install powershell.exe/pwsh.exe.");
    }

    private static string ResolveCmdExecutable()
    {
        var inboxCmd = Path.Combine(Environment.SystemDirectory, "cmd.exe");
        if (File.Exists(inboxCmd))
        {
            return inboxCmd;
        }

        var discovered = ProbeCollector.TryFindCommand("cmd.exe");
        return string.IsNullOrWhiteSpace(discovered) ? "cmd.exe" : discovered;
    }

    private static string ReadScriptFile(string scriptPath)
    {
        if (!File.Exists(scriptPath))
        {
            throw new FileNotFoundException("Script file not found.", scriptPath);
        }

        using var reader = new StreamReader(
            scriptPath,
            new UTF8Encoding(encoderShouldEmitUTF8Identifier: false, throwOnInvalidBytes: false),
            detectEncodingFromByteOrderMarks: true);
        return reader.ReadToEnd();
    }

    private static async Task<int> RunWslProcessAsync(WslProcessOptions options)
    {
        var executable = ResolveWslExecutable();
        var arguments = BuildWslArguments(
            options.WorkingDirectory,
            options.Distribution,
            options.User,
            options.FilePath,
            options.Arguments);

        return await ProcessRunner.RunPassthroughAsync(
            executable,
            arguments,
            workingDirectory: null,
            OutputEncodingPreference.Utf8);
    }

    private static async Task<int> CaptureWslProcessAsync(WslProcessOptions options)
    {
        var executable = ResolveWslExecutable();
        var arguments = BuildWslArguments(
            options.WorkingDirectory,
            options.Distribution,
            options.User,
            options.FilePath,
            options.Arguments);
        var result = await ProcessRunner.RunCaptureAsync(
            executable,
            arguments,
            workingDirectory: null,
            OutputEncodingPreference.Utf8);

        WriteCapturePayload(result);
        return result.ExitCode;
    }

    private static async Task<int> RunWslScriptInternalAsync(WslScriptOptions options)
    {
        var executable = ResolveWslExecutable();
        var tempWindowsPath = WriteTemporaryWslScript(options.ScriptBody);

        try
        {
            var tempWslPath = TranslateWindowsPathToWsl(tempWindowsPath);
            var arguments = BuildWslArguments(
                options.WorkingDirectory,
                options.Distribution,
                options.User,
                options.ShellPath,
                new[] { tempWslPath }.Concat(options.ScriptArguments).ToArray());

            return await ProcessRunner.RunPassthroughAsync(
                executable,
                arguments,
                workingDirectory: null,
                OutputEncodingPreference.Utf8);
        }
        finally
        {
            TryDeleteTemporaryFile(tempWindowsPath);
        }
    }

    private static async Task<int> CaptureWslScriptInternalAsync(WslScriptOptions options)
    {
        var result = await CaptureWslScriptResultAsync(options);
        WriteCapturePayload(result);
        return result.ExitCode;
    }

    private static async Task<ProcessResult> CaptureWslScriptResultAsync(WslScriptOptions options)
    {
        var executable = ResolveWslExecutable();
        var tempWindowsPath = WriteTemporaryWslScript(options.ScriptBody);

        try
        {
            var tempWslPath = TranslateWindowsPathToWsl(tempWindowsPath);
            var arguments = BuildWslArguments(
                options.WorkingDirectory,
                options.Distribution,
                options.User,
                options.ShellPath,
                new[] { tempWslPath }.Concat(options.ScriptArguments).ToArray());
            return await ProcessRunner.RunCaptureAsync(
                executable,
                arguments,
                workingDirectory: null,
                OutputEncodingPreference.Utf8);
        }
        finally
        {
            TryDeleteTemporaryFile(tempWindowsPath);
        }
    }

    private static async Task<int> CaptureWslResidentAsync(WslResidentOptions options)
    {
        var executable = ResolveWslExecutable();
        var bootstrapScriptWindowsPath = WriteTemporaryWslScript(BuildWslResidentBootstrapScript());
        var bootstrapScriptWslPath = TranslateWindowsPathToWsl(bootstrapScriptWindowsPath);
        var launchPath = string.IsNullOrWhiteSpace(options.LaunchPath)
            ? $"/tmp/windows-remote-executor-resident-{Guid.NewGuid():N}.sh"
            : options.LaunchPath!;
        var pidFile = string.IsNullOrWhiteSpace(options.PidFile)
            ? $"/tmp/windows-remote-executor-resident-{Guid.NewGuid():N}.pid"
            : options.PidFile!;
        var logFile = string.IsNullOrWhiteSpace(options.LogFile)
            ? $"/tmp/windows-remote-executor-resident-{Guid.NewGuid():N}.log"
            : options.LogFile!;

        try
        {
            var arguments = BuildWslArguments(
                options.WorkingDirectory,
                options.Distribution,
                options.User,
                "/bin/bash",
                BuildWslResidentArguments(
                    bootstrapScriptWslPath,
                    options,
                    launchPath,
                    pidFile,
                    logFile));

            var result = await ProcessRunner.RunCaptureAsync(
                executable,
                arguments,
                workingDirectory: null,
                OutputEncodingPreference.Utf8);

            var parsedResidentJson = TryNormalizeResidentJson(result.StdOut);
            if (parsedResidentJson is not null)
            {
                Console.WriteLine(parsedResidentJson);
            }
            else
            {
                var payload = new
                {
                    status = result.ExitCode == 0 ? "ok" : "error",
                    exitCode = result.ExitCode,
                    stagePath = options.StagePath,
                    launchPath,
                    pidFile,
                    logFile,
                    stdoutText = result.StdOut,
                    stderrText = result.StdErr
                };
                Console.WriteLine(JsonSerializer.Serialize(payload));
            }

            return result.ExitCode;
        }
        finally
        {
            TryDeleteTemporaryFile(bootstrapScriptWindowsPath);
        }
    }

    private static string ResolveWslExecutable()
    {
        foreach (var candidate in new[] { "wsl.exe", "wsl" })
        {
            var discovered = ProbeCollector.TryFindCommand(candidate);
            if (!string.IsNullOrWhiteSpace(discovered))
            {
                return discovered;
            }
        }

        var inboxWsl = Path.Combine(Environment.SystemDirectory, "wsl.exe");
        if (File.Exists(inboxWsl))
        {
            return inboxWsl;
        }

        throw new InvalidOperationException("No usable wsl.exe found on the Windows host.");
    }

    private static IReadOnlyList<string> BuildWslArguments(
        string? workingDirectory,
        string? distribution,
        string? user,
        string program,
        IReadOnlyList<string> arguments)
    {
        var commandLine = new List<string>();

        if (!string.IsNullOrWhiteSpace(distribution))
        {
            commandLine.Add("--distribution");
            commandLine.Add(distribution);
        }

        if (!string.IsNullOrWhiteSpace(user))
        {
            commandLine.Add("--user");
            commandLine.Add(user);
        }

        if (!string.IsNullOrWhiteSpace(workingDirectory))
        {
            commandLine.Add("--cd");
            commandLine.Add(workingDirectory);
        }

        commandLine.Add("--exec");
        commandLine.Add(program);
        commandLine.AddRange(arguments);
        return commandLine;
    }

    private static IReadOnlyList<string> BuildWslResidentArguments(
        string bootstrapScriptWslPath,
        WslResidentOptions options,
        string launchPath,
        string pidFile,
        string logFile)
    {
        return new[]
        {
            bootstrapScriptWslPath,
            options.StagePath,
            launchPath,
            options.ShellPath,
            pidFile,
            logFile,
            options.Port?.ToString() ?? string.Empty,
            options.HealthUrl ?? string.Empty,
            options.ReadyTimeoutSeconds.ToString(),
            options.SettleDelaySeconds.ToString(),
            options.PollIntervalMilliseconds.ToString(),
            options.DiagnosticLines.ToString()
        }
        .Concat(options.ScriptArguments)
        .ToArray();
    }

    private static string? TryNormalizeResidentJson(string stdout)
    {
        var trimmed = stdout.Trim();
        if (string.IsNullOrWhiteSpace(trimmed))
        {
            return null;
        }

        try
        {
            using var document = JsonDocument.Parse(trimmed);
            return JsonSerializer.Serialize(document.RootElement);
        }
        catch (JsonException)
        {
            return null;
        }
    }

    private static string WriteTemporaryWslScript(string scriptBody)
    {
        var tempPath = Path.Combine(Path.GetTempPath(), $"windows-remote-executor-{Guid.NewGuid():N}.sh");
        var normalized = scriptBody.Replace("\r\n", "\n");
        File.WriteAllText(tempPath, normalized, new UTF8Encoding(false));
        return tempPath;
    }

    private static string TranslateWindowsPathToWsl(string windowsPath)
    {
        var fullPath = Path.GetFullPath(windowsPath);
        if (fullPath.Length < 3 || fullPath[1] != ':')
        {
            throw new InvalidOperationException($"Cannot translate non-drive Windows path to WSL: {windowsPath}");
        }

        var drive = char.ToLowerInvariant(fullPath[0]);
        var remainder = fullPath[2..].Replace('\\', '/');
        return $"/mnt/{drive}{remainder}";
    }

    private static void TryDeleteTemporaryFile(string path)
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
            // Best effort cleanup for transient WSL script files.
        }
    }

    private static void WriteCapturePayload(ProcessResult result)
    {
        var payload = new
        {
            exitCode = result.ExitCode,
            stdoutText = result.StdOut,
            stderrText = result.StdErr,
            stdoutEncoding = result.StdOutEncoding,
            stderrEncoding = result.StdErrEncoding,
            stdoutBase64 = Convert.ToBase64String(result.StdOutBytes),
            stderrBase64 = Convert.ToBase64String(result.StdErrBytes),
            stdoutBytes = result.StdOutBytes.Length,
            stderrBytes = result.StdErrBytes.Length
        };
        Console.WriteLine(JsonSerializer.Serialize(payload));
    }

    private static string ComposePowerShellScript(string body)
    {
        return string.Join(
            Environment.NewLine,
            new[]
            {
                "$ErrorActionPreference = 'Stop'",
                "$ProgressPreference = 'SilentlyContinue'",
                "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)",
                "$OutputEncoding = [Console]::OutputEncoding",
                "try { chcp 65001 > $null } catch {}",
                body
            });
    }

    private static string BuildWslResidentBootstrapScript()
    {
        return
            """
            set -euo pipefail

            src="$1"
            dst="$2"
            shell_path="$3"
            pid_file="$4"
            log_file="$5"
            port="$6"
            health_url="$7"
            ready_timeout="$8"
            settle_delay="$9"
            poll_interval_ms="${10}"
            diag_lines="${11}"
            shift 11

            mkdir -p "$(dirname "$dst")" "$(dirname "$pid_file")" "$(dirname "$log_file")"

            cleanup() {
              rm -f "$dst"
            }

            trap cleanup EXIT

            cp "$src" "$dst"
            chmod 700 "$dst"
            touch "$log_file"
            rm -f "$pid_file"

            launch_method="setsid"
            if command -v setsid >/dev/null 2>&1; then
              setsid "$shell_path" "$dst" "$@" >>"$log_file" 2>&1 < /dev/null &
            else
              launch_method="nohup"
              nohup "$shell_path" "$dst" "$@" >>"$log_file" 2>&1 < /dev/null &
            fi
            pid="$!"
            printf '%s\n' "$pid" >"$pid_file"

            poll_seconds="$(printf '%d.%03d' "$((poll_interval_ms / 1000))" "$((poll_interval_ms % 1000))")"

            check_port() {
              if [[ -z "$port" ]]; then
                return 0
              fi

              if command -v ss >/dev/null 2>&1; then
                ss -ltnH 2>/dev/null | awk '{print $4}' | grep -Eq "(^|[:])${port}$"
                return $?
              fi

              return 1
            }

            check_health() {
              if [[ -z "$health_url" ]]; then
                return 0
              fi

              if command -v curl >/dev/null 2>&1; then
                curl -fsS --max-time 2 "$health_url" >/dev/null
                return $?
              fi

              if command -v wget >/dev/null 2>&1; then
                wget -q -T 2 -O /dev/null "$health_url"
                return $?
              fi

              if command -v python3 >/dev/null 2>&1; then
                python3 - "$health_url" <<'PY'
            import sys
            import urllib.request

            url = sys.argv[1]
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status >= 400:
                    raise SystemExit(1)
            PY
                return $?
              fi

              return 1
            }

            if [[ "$settle_delay" != "0" ]]; then
              sleep "$settle_delay"
            fi

            deadline="$(( $(date +%s) + ready_timeout ))"
            pid_alive=0
            port_ready=0
            health_ready=0
            ready=0

            while true; do
              if kill -0 "$pid" 2>/dev/null; then
                pid_alive=1
              else
                pid_alive=0
              fi

              if check_port; then
                port_ready=1
              else
                port_ready=0
              fi

              if check_health; then
                health_ready=1
              else
                health_ready=0
              fi

              if [[ "$pid_alive" == "1" && "$port_ready" == "1" && "$health_ready" == "1" ]]; then
                ready=1
                break
              fi

              if [[ "$(date +%s)" -ge "$deadline" ]]; then
                break
              fi

              sleep "$poll_seconds"
            done

            command_line=""
            process_cwd=""
            if kill -0 "$pid" 2>/dev/null; then
              command_line="$(tr '\0' ' ' <"/proc/${pid}/cmdline" 2>/dev/null || true)"
              process_cwd="$(readlink -f "/proc/${pid}/cwd" 2>/dev/null || true)"
            fi

            listener_snapshot=""
            if [[ -n "$port" ]] && command -v ss >/dev/null 2>&1; then
              listener_snapshot="$(ss -ltnp 2>/dev/null | awk -v port="${port}" '$4 ~ (":" port "$") { print }' | tail -n "$diag_lines" || true)"
            fi

            log_tail="$(tail -n "$diag_lines" "$log_file" 2>/dev/null || true)"
            status="error"
            exit_code=1
            if [[ "$ready" == "1" ]]; then
              status="ok"
              exit_code=0
            fi

            if command -v python3 >/dev/null 2>&1; then
              python3 - \
                "$status" \
                "$exit_code" \
                "$launch_method" \
                "$pid" \
                "$pid_file" \
                "$log_file" \
                "$src" \
                "$dst" \
                "$port" \
                "$health_url" \
                "$ready_timeout" \
                "$settle_delay" \
                "$poll_interval_ms" \
                "$diag_lines" \
                "$pid_alive" \
                "$port_ready" \
                "$health_ready" \
                "$command_line" \
                "$process_cwd" \
                "$listener_snapshot" \
                "$log_tail" <<'PY'
            import json
            import sys

            (
                status,
                exit_code,
                launch_method,
                pid,
                pid_file,
                log_file,
                stage_path,
                launch_path,
                port,
                health_url,
                ready_timeout,
                settle_delay,
                poll_interval_ms,
                diagnostic_lines,
                pid_alive,
                port_ready,
                health_ready,
                command_line,
                process_cwd,
                listener_snapshot,
                log_tail,
            ) = sys.argv[1:]

            payload = {
                "status": status,
                "exitCode": int(exit_code),
                "launchMethod": launch_method,
                "pid": int(pid) if pid else None,
                "pidFile": pid_file,
                "logFile": log_file,
                "stagePath": stage_path,
                "launchPath": launch_path,
                "port": int(port) if port else None,
                "healthUrl": health_url or None,
                "readyTimeoutSeconds": int(ready_timeout),
                "settleDelaySeconds": int(settle_delay),
                "pollIntervalMilliseconds": int(poll_interval_ms),
                "diagnosticLines": int(diagnostic_lines),
                "pidAlive": pid_alive == "1",
                "portReady": port_ready == "1",
                "healthReady": health_ready == "1",
                "commandLine": command_line,
                "workingDirectory": process_cwd,
                "listenerSnapshot": listener_snapshot.splitlines(),
                "logTail": log_tail.splitlines(),
            }

            print(json.dumps(payload, ensure_ascii=False))
            PY
            else
              printf '{"status":"%s","exitCode":%s,"pid":%s,"pidFile":"%s","logFile":"%s"}\n' \
                "$status" "$exit_code" "${pid:-null}" "$pid_file" "$log_file"
            fi

            exit "$exit_code"
            """;
    }
}

internal static class Base64Args
{
    public static string ReadValue(string[] args, ref int index, string option)
    {
        if (index + 1 >= args.Length)
        {
            throw new ArgumentException($"{option} requires a value.");
        }

        index++;
        return Decode(args[index]);
    }

    public static string Decode(string value)
    {
        return Encoding.UTF8.GetString(Convert.FromBase64String(value));
    }
}

internal sealed record ResolvedExecutable(string FilePath, IReadOnlyList<string> AdditionalArguments);
