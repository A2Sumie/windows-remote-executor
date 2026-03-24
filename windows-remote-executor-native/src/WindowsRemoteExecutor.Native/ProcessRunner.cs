using System.Diagnostics;
using System.Text;

namespace WindowsRemoteExecutor.Native;

internal sealed class ProcessResult
{
    public int ExitCode { get; init; }
    public string StdOut { get; init; } = string.Empty;
    public string StdErr { get; init; } = string.Empty;
}

internal static class ProcessRunner
{
    public static async Task<ProcessResult> RunAsync(string fileName, string arguments, bool throwOnFailure)
    {
        var startInfo = new ProcessStartInfo
        {
            FileName = fileName,
            Arguments = arguments,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8
        };

        using var process = new Process
        {
            StartInfo = startInfo
        };

        return await RunForResultAsync(process, $"{fileName} {arguments}", throwOnFailure);
    }

    public static async Task<ProcessResult> RunAsync(string fileName, IReadOnlyList<string> arguments, bool throwOnFailure)
    {
        var startInfo = new ProcessStartInfo
        {
            FileName = fileName,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8
        };

        foreach (var argument in arguments)
        {
            startInfo.ArgumentList.Add(argument);
        }

        using var process = new Process
        {
            StartInfo = startInfo
        };

        var display = string.Join(" ", new[] { fileName }.Concat(arguments.Select(QuoteForDisplay)));
        return await RunForResultAsync(process, display, throwOnFailure);
    }

    public static async Task<int> RunPassthroughAsync(string fileName, IReadOnlyList<string> arguments, string? workingDirectory)
    {
        var startInfo = new ProcessStartInfo
        {
            FileName = fileName,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8
        };

        if (!string.IsNullOrWhiteSpace(workingDirectory))
        {
            startInfo.WorkingDirectory = workingDirectory;
        }

        foreach (var argument in arguments)
        {
            startInfo.ArgumentList.Add(argument);
        }

        using var process = new Process
        {
            StartInfo = startInfo
        };

        process.Start();
        var stdoutTask = PumpAsync(process.StandardOutput, Console.Out);
        var stderrTask = PumpAsync(process.StandardError, Console.Error);
        await Task.WhenAll(stdoutTask, stderrTask, process.WaitForExitAsync());
        return process.ExitCode;
    }

    private static async Task PumpAsync(StreamReader reader, TextWriter writer)
    {
        var buffer = new char[4096];
        while (true)
        {
            var read = await reader.ReadAsync(buffer, 0, buffer.Length);
            if (read <= 0)
            {
                break;
            }

            await writer.WriteAsync(buffer, 0, read);
            await writer.FlushAsync();
        }
    }

    private static async Task<ProcessResult> RunForResultAsync(Process process, string displayCommand, bool throwOnFailure)
    {
        process.Start();
        var stdoutTask = process.StandardOutput.ReadToEndAsync();
        var stderrTask = process.StandardError.ReadToEndAsync();
        await process.WaitForExitAsync();

        var result = new ProcessResult
        {
            ExitCode = process.ExitCode,
            StdOut = await stdoutTask,
            StdErr = await stderrTask
        };

        if (throwOnFailure && result.ExitCode != 0)
        {
            throw new InvalidOperationException(
                $"Command failed: {displayCommand}{Environment.NewLine}exit={result.ExitCode}{Environment.NewLine}{result.StdOut}{Environment.NewLine}{result.StdErr}");
        }

        return result;
    }

    private static string QuoteForDisplay(string argument)
    {
        if (argument.Length == 0)
        {
            return "\"\"";
        }

        return argument.Any(char.IsWhiteSpace) || argument.Contains('"')
            ? "\"" + argument.Replace("\"", "\\\"") + "\""
            : argument;
    }
}
