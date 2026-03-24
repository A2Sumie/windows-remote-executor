using System.Runtime.InteropServices;
using System.Text;

namespace WindowsRemoteExecutor.Native;

internal sealed class EverythingSearchOptions
{
    public string Query { get; init; } = string.Empty;
    public uint MaxResults { get; init; } = 100;

    public static EverythingSearchOptions FromBase64Args(string[] args)
    {
        string? query = null;
        uint maxResults = 100;

        for (var i = 0; i < args.Length; i++)
        {
            switch (args[i])
            {
                case "--query":
                    query = Base64Args.ReadValue(args, ref i, "--query");
                    break;
                case "--max":
                    if (i + 1 >= args.Length || !uint.TryParse(args[++i], out maxResults))
                    {
                        throw new ArgumentException("--max requires an unsigned integer value.");
                    }
                    break;
                default:
                    throw new ArgumentException($"Unknown everything option: {args[i]}");
            }
        }

        if (string.IsNullOrWhiteSpace(query))
        {
            throw new ArgumentException("--query is required.");
        }

        return new EverythingSearchOptions
        {
            Query = query,
            MaxResults = maxResults
        };
    }
}

internal static class EverythingSearch
{
    private const string EverythingDll = "Everything64.dll";
    private const uint EverythingRequestFileName = 0x00000001;
    private const uint EverythingRequestPath = 0x00000002;
    private const uint EverythingSortPathAscending = 3;
    private const int EverythingOk = 0;
    private const int EverythingErrorMemory = 1;
    private const int EverythingErrorIpc = 2;
    private const int EverythingErrorRegisterClassEx = 3;
    private const int EverythingErrorCreateWindow = 4;
    private const int EverythingErrorCreateThread = 5;
    private const int EverythingErrorInvalidIndex = 6;
    private const int EverythingErrorInvalidCall = 7;

    public static int SearchToStdout(string[] args)
    {
        var options = EverythingSearchOptions.FromBase64Args(args);
        EnsureSdkPresent();

        Everything_Reset();
        Everything_SetSearchW(options.Query);
        Everything_SetRequestFlags(EverythingRequestFileName | EverythingRequestPath);
        Everything_SetSort(EverythingSortPathAscending);
        Everything_SetMax(options.MaxResults);

        if (!Everything_QueryW(true))
        {
            throw new InvalidOperationException($"Everything query failed: {GetLastErrorName(Everything_GetLastError())}.");
        }

        var resultCount = Everything_GetNumResults();
        for (uint i = 0; i < resultCount; i++)
        {
            Console.WriteLine(GetResultFullPath(i));
        }

        return 0;
    }

    private static void EnsureSdkPresent()
    {
        var dllPath = Path.Combine(AppContext.BaseDirectory, EverythingDll);
        if (!File.Exists(dllPath))
        {
            throw new FileNotFoundException($"Everything SDK DLL not found next to executable: {dllPath}");
        }
    }

    private static string GetResultFullPath(uint index)
    {
        var buffer = new StringBuilder(32768);
        Everything_GetResultFullPathName(index, buffer, (uint)buffer.Capacity);
        return buffer.ToString();
    }

    private static string GetLastErrorName(uint code)
    {
        return code switch
        {
            EverythingOk => "EVERYTHING_OK",
            EverythingErrorMemory => "EVERYTHING_ERROR_MEMORY",
            EverythingErrorIpc => "EVERYTHING_ERROR_IPC",
            EverythingErrorRegisterClassEx => "EVERYTHING_ERROR_REGISTERCLASSEX",
            EverythingErrorCreateWindow => "EVERYTHING_ERROR_CREATEWINDOW",
            EverythingErrorCreateThread => "EVERYTHING_ERROR_CREATETHREAD",
            EverythingErrorInvalidIndex => "EVERYTHING_ERROR_INVALIDINDEX",
            EverythingErrorInvalidCall => "EVERYTHING_ERROR_INVALIDCALL",
            _ => $"EVERYTHING_ERROR_{code}"
        };
    }

    [DllImport(EverythingDll, CharSet = CharSet.Unicode)]
    private static extern void Everything_SetSearchW(string lpSearchString);

    [DllImport(EverythingDll)]
    private static extern void Everything_SetRequestFlags(uint dwRequestFlags);

    [DllImport(EverythingDll)]
    private static extern void Everything_SetSort(uint dwSortType);

    [DllImport(EverythingDll)]
    private static extern void Everything_SetMax(uint dwMax);

    [DllImport(EverythingDll)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool Everything_QueryW([MarshalAs(UnmanagedType.Bool)] bool bWait);

    [DllImport(EverythingDll)]
    private static extern uint Everything_GetNumResults();

    [DllImport(EverythingDll, CharSet = CharSet.Unicode)]
    private static extern void Everything_GetResultFullPathName(uint nIndex, StringBuilder lpString, uint nMaxCount);

    [DllImport(EverythingDll)]
    private static extern uint Everything_GetLastError();

    [DllImport(EverythingDll)]
    private static extern void Everything_Reset();
}
