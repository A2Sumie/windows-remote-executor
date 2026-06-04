using System.Text.Json;

namespace WindowsRemoteExecutor.Native;

internal sealed class FilePathOptions
{
    public string Path { get; init; } = string.Empty;

    public static FilePathOptions FromBase64Args(string[] args, string commandName)
    {
        var path = string.Empty;

        for (var i = 0; i < args.Length; i++)
        {
            switch (args[i])
            {
                case "--path":
                    path = Base64Args.ReadValue(args, ref i, "--path");
                    break;
                default:
                    throw new ArgumentException($"Unknown {commandName} option: {args[i]}");
            }
        }

        if (string.IsNullOrWhiteSpace(path))
        {
            throw new ArgumentException("--path is required.");
        }

        return new FilePathOptions { Path = path };
    }
}

internal sealed class CopyFileOptions
{
    public string Source { get; init; } = string.Empty;
    public string Destination { get; init; } = string.Empty;

    public static CopyFileOptions FromBase64Args(string[] args)
    {
        var source = string.Empty;
        var destination = string.Empty;

        for (var i = 0; i < args.Length; i++)
        {
            switch (args[i])
            {
                case "--source":
                    source = Base64Args.ReadValue(args, ref i, "--source");
                    break;
                case "--destination":
                    destination = Base64Args.ReadValue(args, ref i, "--destination");
                    break;
                default:
                    throw new ArgumentException($"Unknown copy-file option: {args[i]}");
            }
        }

        if (string.IsNullOrWhiteSpace(source))
        {
            throw new ArgumentException("--source is required.");
        }

        if (string.IsNullOrWhiteSpace(destination))
        {
            throw new ArgumentException("--destination is required.");
        }

        return new CopyFileOptions
        {
            Source = source,
            Destination = destination
        };
    }
}

internal static class FileCommands
{
    public static int EnsureDirectory(string[] args)
    {
        var options = FilePathOptions.FromBase64Args(args, "mkdir");
        Directory.CreateDirectory(options.Path);
        WriteOk(new { path = options.Path });
        return 0;
    }

    public static int DeleteTree(string[] args)
    {
        var options = FilePathOptions.FromBase64Args(args, "delete-tree");
        if (Directory.Exists(options.Path))
        {
            Directory.Delete(options.Path, recursive: true);
        }
        else if (File.Exists(options.Path))
        {
            File.Delete(options.Path);
        }

        WriteOk(new { path = options.Path });
        return 0;
    }

    public static int CopyFile(string[] args)
    {
        var options = CopyFileOptions.FromBase64Args(args);
        var parent = Path.GetDirectoryName(options.Destination);
        if (!string.IsNullOrWhiteSpace(parent))
        {
            Directory.CreateDirectory(parent);
        }

        File.Copy(options.Source, options.Destination, overwrite: true);
        WriteOk(new { source = options.Source, destination = options.Destination });
        return 0;
    }

    private static void WriteOk(object details)
    {
        Console.WriteLine(JsonSerializer.Serialize(new
        {
            status = "ok",
            details
        }));
    }
}
