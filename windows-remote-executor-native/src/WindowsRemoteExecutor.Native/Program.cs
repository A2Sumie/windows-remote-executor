using System.Text.Json;
using System.Text;

namespace WindowsRemoteExecutor.Native;

internal static class Program
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        WriteIndented = true
    };

    public static async Task<int> Main(string[] args)
    {
        try
        {
            Encoding.RegisterProvider(CodePagesEncodingProvider.Instance);
            Console.InputEncoding = new UTF8Encoding(encoderShouldEmitUTF8Identifier: false);
            Console.OutputEncoding = new UTF8Encoding(encoderShouldEmitUTF8Identifier: false);

            if (args.Length == 0 || IsHelp(args[0]))
            {
                PrintUsage();
                return 0;
            }

            var command = args[0].Trim().ToLowerInvariant();
            var securityContext = ExecutorAccessControl.Extract(args.Skip(1).ToArray());
            var commandArgs = securityContext.RemainingArgs;

            if (command is "selftest" or "rpc-selftest")
            {
                Console.WriteLine(RpcServer.BuildSelfTestJson());
                return 0;
            }

            if (!OperatingSystem.IsWindows())
            {
                Console.Error.WriteLine("This executable only runs on Windows. Use selftest for cross-platform envelope validation.");
                return 1;
            }

            switch (command)
            {
                case "rpc-stdio":
                    return await RpcServer.RunStdioAsync();

                case "bootstrap":
                    var bootstrapOptions = BootstrapOptions.FromArgs(commandArgs);
                    var bootstrapResult = await Bootstrapper.RunBootstrapAsync(bootstrapOptions);
                    Console.WriteLine(JsonSerializer.Serialize(bootstrapResult, JsonOptions));
                    return 0;

                case "guard-sshd":
                    return await SshExposureGuard.RunCommandAsync(commandArgs);

                case "repair-sshd":
                    return await SshRepair.RunCommandAsync(commandArgs);

                default:
                    Console.Error.WriteLine($"Unknown command: {args[0]}");
                    PrintUsage();
                    return 1;
            }
        }
        catch (ArgumentException ex)
        {
            Console.Error.WriteLine(ex.Message);
            PrintUsage();
            return 2;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(ex);
            return 1;
        }
    }

    private static bool IsHelp(string value) =>
        value is "-h" or "--help" or "help";

    private static void PrintUsage()
    {
        Console.WriteLine(
            """
            WindowsRemoteExecutor.Native

            Usage:
              WindowsRemoteExecutor.Native.exe bootstrap [options]
              WindowsRemoteExecutor.Native.exe guard-sshd [options]
              WindowsRemoteExecutor.Native.exe repair-sshd [options]
              WindowsRemoteExecutor.Native.exe selftest
              WindowsRemoteExecutor.Native.exe rpc-selftest
              WindowsRemoteExecutor.Native.exe rpc-stdio

            bootstrap options:
              --authorized-key <public-key>
              --public-key-file <path>
              --user <username>
              --listen-address <ip>
              --codex-root <path>
              --set-powershell-default-shell
              --clear-default-shell
              --install-tailscale

            guard-sshd options:
              --expected-listen-address <ip>
              --log-path <path>
              --no-disable

            repair-sshd options:
              --expected-listen-address <ip>
              --codex-root <path>
              --log-path <path>
              --force-rewrite

            rpc-stdio request:
              One UTF-8 JSON object on stdin. The response is one UTF-8 JSON object
              on stdout. Supported actions are listed by host.capabilities.
            """);
    }
}
