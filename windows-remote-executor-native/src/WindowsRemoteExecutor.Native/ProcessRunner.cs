using System.Diagnostics;
using System.Text;

namespace WindowsRemoteExecutor.Native;

internal sealed class ProcessResult
{
    public int ExitCode { get; init; }
    public string StdOut { get; init; } = string.Empty;
    public string StdErr { get; init; } = string.Empty;
    public string StdOutEncoding { get; init; } = "utf-8";
    public string StdErrEncoding { get; init; } = "utf-8";
    public byte[] StdOutBytes { get; init; } = Array.Empty<byte>();
    public byte[] StdErrBytes { get; init; } = Array.Empty<byte>();
}

internal static class ProcessRunner
{
    public static async Task<ProcessResult> RunAsync(
        string fileName,
        string arguments,
        bool throwOnFailure,
        OutputEncodingPreference outputPreference = OutputEncodingPreference.Auto)
    {
        var startInfo = CreateStartInfo(fileName, workingDirectory: null, environmentOverrides: null);
        startInfo.Arguments = arguments;

        using var process = new Process
        {
            StartInfo = startInfo
        };

        return await RunForResultAsync(process, $"{fileName} {arguments}", throwOnFailure, outputPreference);
    }

    public static async Task<ProcessResult> RunAsync(
        string fileName,
        IReadOnlyList<string> arguments,
        bool throwOnFailure,
        OutputEncodingPreference outputPreference = OutputEncodingPreference.Auto,
        IReadOnlyDictionary<string, string?>? environmentOverrides = null)
    {
        var startInfo = CreateStartInfo(fileName, workingDirectory: null, environmentOverrides);

        foreach (var argument in arguments)
        {
            startInfo.ArgumentList.Add(argument);
        }

        using var process = new Process
        {
            StartInfo = startInfo
        };

        var display = string.Join(" ", new[] { fileName }.Concat(arguments.Select(QuoteForDisplay)));
        return await RunForResultAsync(process, display, throwOnFailure, outputPreference);
    }

    public static async Task<int> RunPassthroughAsync(
        string fileName,
        IReadOnlyList<string> arguments,
        string? workingDirectory,
        OutputEncodingPreference outputPreference = OutputEncodingPreference.Auto,
        IReadOnlyDictionary<string, string?>? environmentOverrides = null)
    {
        var startInfo = CreateStartInfo(fileName, workingDirectory, environmentOverrides);

        foreach (var argument in arguments)
        {
            startInfo.ArgumentList.Add(argument);
        }

        using var process = new Process
        {
            StartInfo = startInfo
        };

        process.Start();
        var stdoutTask = PumpAsync(process.StandardOutput.BaseStream, Console.Out, outputPreference);
        var stderrTask = PumpAsync(process.StandardError.BaseStream, Console.Error, outputPreference);
        await Task.WhenAll(stdoutTask, stderrTask, process.WaitForExitAsync());
        return process.ExitCode;
    }

    public static async Task<ProcessResult> RunCaptureAsync(
        string fileName,
        IReadOnlyList<string> arguments,
        string? workingDirectory,
        OutputEncodingPreference outputPreference = OutputEncodingPreference.Auto,
        IReadOnlyDictionary<string, string?>? environmentOverrides = null)
    {
        var startInfo = CreateStartInfo(fileName, workingDirectory, environmentOverrides);
        foreach (var argument in arguments)
        {
            startInfo.ArgumentList.Add(argument);
        }

        using var process = new Process
        {
            StartInfo = startInfo
        };

        var display = string.Join(" ", new[] { fileName }.Concat(arguments.Select(QuoteForDisplay)));
        return await RunForResultAsync(process, display, throwOnFailure: false, outputPreference);
    }

    private static ProcessStartInfo CreateStartInfo(
        string fileName,
        string? workingDirectory,
        IReadOnlyDictionary<string, string?>? environmentOverrides)
    {
        var startInfo = new ProcessStartInfo
        {
            FileName = fileName,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true
        };

        if (!string.IsNullOrWhiteSpace(workingDirectory))
        {
            startInfo.WorkingDirectory = workingDirectory;
        }

        if (environmentOverrides is not null)
        {
            foreach (var pair in environmentOverrides)
            {
                startInfo.Environment[pair.Key] = pair.Value;
            }
        }

        return startInfo;
    }

    private static async Task PumpAsync(Stream input, TextWriter writer, OutputEncodingPreference outputPreference)
    {
        using var initialBuffer = new MemoryStream();
        var byteBuffer = new byte[4096];
        Decoder? decoder = null;
        var skipBom = false;

        while (true)
        {
            var read = await input.ReadAsync(byteBuffer, 0, byteBuffer.Length);
            if (read <= 0)
            {
                break;
            }

            if (decoder is null)
            {
                initialBuffer.Write(byteBuffer, 0, read);
                if (!ShouldFinalizeProbe(initialBuffer))
                {
                    continue;
                }

                var detected = OutputDecoding.DetectEncoding(initialBuffer.ToArray(), outputPreference);
                decoder = detected.Encoding.GetDecoder();
                skipBom = true;
                skipBom = await WriteDecodedBytesAsync(writer, decoder, initialBuffer.ToArray(), flush: false, skipBom);
                initialBuffer.SetLength(0);
                continue;
            }

            skipBom = await WriteDecodedBytesAsync(writer, decoder, byteBuffer.AsSpan(0, read).ToArray(), flush: false, skipBom);
        }

        if (decoder is null)
        {
            if (initialBuffer.Length == 0)
            {
                return;
            }

            var detected = OutputDecoding.DetectEncoding(initialBuffer.ToArray(), outputPreference);
            decoder = detected.Encoding.GetDecoder();
            skipBom = true;
            _ = await WriteDecodedBytesAsync(writer, decoder, initialBuffer.ToArray(), flush: true, skipBom);
            return;
        }

        _ = await WriteDecodedBytesAsync(writer, decoder, Array.Empty<byte>(), flush: true, skipBom);
    }

    private static bool ShouldFinalizeProbe(MemoryStream initialBuffer)
    {
        var bytes = initialBuffer.GetBuffer().AsSpan(0, checked((int)initialBuffer.Length));
        if (bytes.Length >= 2048)
        {
            return true;
        }

        if (bytes.IndexOf((byte)'\n') >= 0 || bytes.IndexOf((byte)'\r') >= 0)
        {
            return true;
        }

        if (bytes.Length >= 4 &&
            (bytes.StartsWith(new byte[] { 0xEF, 0xBB, 0xBF }) ||
             bytes.StartsWith(new byte[] { 0xFF, 0xFE }) ||
             bytes.StartsWith(new byte[] { 0xFE, 0xFF })))
        {
            return true;
        }

        if (bytes.Length >= 64)
        {
            return true;
        }

        return false;
    }

    private static async Task<bool> WriteDecodedBytesAsync(TextWriter writer, Decoder decoder, byte[] bytes, bool flush, bool skipBom)
    {
        var charCount = decoder.GetCharCount(bytes, 0, bytes.Length, flush);
        if (charCount == 0)
        {
            if (flush)
            {
                await writer.FlushAsync();
            }

            return skipBom;
        }

        var chars = new char[charCount];
        var written = decoder.GetChars(bytes, 0, bytes.Length, chars, 0, flush);
        var text = new string(chars, 0, written);
        if (skipBom && text.Length > 0 && text[0] == '\uFEFF')
        {
            text = text[1..];
        }

        skipBom = false;
        if (text.Length > 0)
        {
            await writer.WriteAsync(text);
        }

        if (flush)
        {
            await writer.FlushAsync();
        }

        return skipBom;
    }

    private static async Task<ProcessResult> RunForResultAsync(
        Process process,
        string displayCommand,
        bool throwOnFailure,
        OutputEncodingPreference outputPreference)
    {
        process.Start();
        var stdoutTask = ReadBytesAsync(process.StandardOutput.BaseStream);
        var stderrTask = ReadBytesAsync(process.StandardError.BaseStream);
        await process.WaitForExitAsync();
        var stdoutBytes = await stdoutTask;
        var stderrBytes = await stderrTask;
        var decodedStdOut = OutputDecoding.Decode(stdoutBytes, outputPreference);
        var decodedStdErr = OutputDecoding.Decode(stderrBytes, outputPreference);

        var result = new ProcessResult
        {
            ExitCode = process.ExitCode,
            StdOut = decodedStdOut.Text,
            StdErr = decodedStdErr.Text,
            StdOutEncoding = decodedStdOut.EncodingLabel,
            StdErrEncoding = decodedStdErr.EncodingLabel,
            StdOutBytes = stdoutBytes,
            StdErrBytes = stderrBytes
        };

        if (throwOnFailure && result.ExitCode != 0)
        {
            throw new InvalidOperationException(
                $"Command failed: {displayCommand}{Environment.NewLine}exit={result.ExitCode}{Environment.NewLine}{result.StdOut}{Environment.NewLine}{result.StdErr}");
        }

        return result;
    }

    private static async Task<byte[]> ReadBytesAsync(Stream input)
    {
        using var buffer = new MemoryStream();
        await input.CopyToAsync(buffer);
        return buffer.ToArray();
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
