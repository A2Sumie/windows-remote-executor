using System.Text.RegularExpressions;

namespace WindowsRemoteExecutor.Native;

internal static partial class PathPolicy
{
    public static string NormalizeWindowsPath(string path, string fieldName)
    {
        EnsureSafePathShape(path, fieldName);
        return path.Replace('/', Path.DirectorySeparatorChar);
    }

    public static void EnsureSafePathShape(string path, string fieldName)
    {
        if (string.IsNullOrWhiteSpace(path))
        {
            throw new ArgumentException($"{fieldName} is required.");
        }

        if (DriveRelativePattern().IsMatch(path))
        {
            throw new ArgumentException(
                $"Suspicious Windows drive-relative path for {fieldName}: {path}. Use forward slashes such as D:/path/file or a fully qualified backslash path.");
        }

        if (path.Contains('\0'))
        {
            throw new ArgumentException($"{fieldName} contains a NUL character.");
        }
    }

    [GeneratedRegex("^[A-Za-z]:($|[^/\\\\])")]
    private static partial Regex DriveRelativePattern();
}
