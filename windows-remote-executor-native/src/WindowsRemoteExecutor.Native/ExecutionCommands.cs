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

internal static class ExecutionCommands
{
    public static async Task<int> RunCommandAsync(string[] args)
    {
        var options = RunProcessOptions.FromBase64Args(args);
        return await ProcessRunner.RunPassthroughAsync(options.FilePath, options.Arguments, options.WorkingDirectory);
    }

    public static async Task<int> RunPythonAsync(string[] args)
    {
        var options = PythonScriptOptions.FromBase64Args(args);
        var plan = ResolvePythonExecution(options);
        return await ProcessRunner.RunPassthroughAsync(plan.FilePath, plan.Arguments, plan.WorkingDirectory);
    }

    public static async Task<int> RunPowerShellAsync(string[] args)
    {
        var options = PowerShellScriptOptions.FromBase64Args(args);
        var executable = ResolvePowerShellExecutable(options.PowerShellExecutable);
        var wrappedScript = ComposePowerShellScript(options.ScriptBody);
        var encodedCommand = Convert.ToBase64String(Encoding.Unicode.GetBytes(wrappedScript));

        return await ProcessRunner.RunPassthroughAsync(
            executable,
            new[]
            {
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-EncodedCommand",
                encodedCommand
            },
            options.WorkingDirectory);
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
