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

            if (!OperatingSystem.IsWindows())
            {
                Console.Error.WriteLine("This executable only runs on Windows.");
                return 1;
            }

            var command = args[0].Trim().ToLowerInvariant();
            var securityContext = ExecutorAccessControl.Extract(args.Skip(1).ToArray());
            var commandArgs = securityContext.RemainingArgs;

            switch (command)
            {
                case "bootstrap":
                case "bootstrap-x570":
                    var bootstrapOptions = BootstrapOptions.FromArgs(commandArgs);
                    var bootstrapResult = await Bootstrapper.RunBootstrapAsync(bootstrapOptions);
                    Console.WriteLine(JsonSerializer.Serialize(bootstrapResult, JsonOptions));
                    return 0;

                case "guard-sshd":
                    return await SshExposureGuard.RunCommandAsync(commandArgs);

                case "repair-sshd":
                    return await SshRepair.RunCommandAsync(commandArgs);

                case "probe":
                    ExecutorAccessControl.EnsureCommandAllowed(command, securityContext.AccessToken);
                    var probe = ProbeCollector.Collect();
                    Console.WriteLine(JsonSerializer.Serialize(probe, JsonOptions));
                    return 0;

                case "run-b64":
                    ExecutorAccessControl.EnsureCommandAllowed(command, securityContext.AccessToken);
                    return await ExecutionCommands.RunCommandAsync(commandArgs);

                case "capture-b64":
                    ExecutorAccessControl.EnsureCommandAllowed(command, securityContext.AccessToken);
                    return await ExecutionCommands.CaptureCommandAsync(commandArgs);

                case "python-b64":
                    ExecutorAccessControl.EnsureCommandAllowed(command, securityContext.AccessToken);
                    return await ExecutionCommands.RunPythonAsync(commandArgs);

                case "powershell-b64":
                    ExecutorAccessControl.EnsureCommandAllowed(command, securityContext.AccessToken);
                    return await ExecutionCommands.RunPowerShellAsync(commandArgs);

                case "wsl-b64":
                    ExecutorAccessControl.EnsureCommandAllowed(command, securityContext.AccessToken);
                    return await ExecutionCommands.RunWslAsync(commandArgs);

                case "wsl-capture-b64":
                    ExecutorAccessControl.EnsureCommandAllowed(command, securityContext.AccessToken);
                    return await ExecutionCommands.CaptureWslAsync(commandArgs);

                case "wsl-script-b64":
                    ExecutorAccessControl.EnsureCommandAllowed(command, securityContext.AccessToken);
                    return await ExecutionCommands.RunWslScriptAsync(commandArgs);

                case "wsl-script-capture-b64":
                    ExecutorAccessControl.EnsureCommandAllowed(command, securityContext.AccessToken);
                    return await ExecutionCommands.CaptureWslScriptAsync(commandArgs);

                case "everything-b64":
                    ExecutorAccessControl.EnsureCommandAllowed(command, securityContext.AccessToken);
                    return EverythingSearch.SearchToStdout(commandArgs);

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
              WindowsRemoteExecutor.Native.exe bootstrap-x570 [options]   (legacy alias)
              WindowsRemoteExecutor.Native.exe guard-sshd [options]
              WindowsRemoteExecutor.Native.exe repair-sshd [options]
              WindowsRemoteExecutor.Native.exe probe
              WindowsRemoteExecutor.Native.exe run-b64 [options]
              WindowsRemoteExecutor.Native.exe capture-b64 [options]
              WindowsRemoteExecutor.Native.exe python-b64 [options]
              WindowsRemoteExecutor.Native.exe powershell-b64 [options]
              WindowsRemoteExecutor.Native.exe wsl-b64 [options]
              WindowsRemoteExecutor.Native.exe wsl-capture-b64 [options]
              WindowsRemoteExecutor.Native.exe wsl-script-b64 [options]
              WindowsRemoteExecutor.Native.exe wsl-script-capture-b64 [options]
              WindowsRemoteExecutor.Native.exe everything-b64 [options]

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

            run-b64 options:
              --file <base64-utf8-path-or-command>
              --cwd <base64-utf8-working-directory>
              --arg <base64-utf8-argument>

            capture-b64 options:
              --file <base64-utf8-path-or-command>
              --cwd <base64-utf8-working-directory>
              --arg <base64-utf8-argument>

            python-b64 options:
              --script <base64-utf8-script-path>
              --cwd <base64-utf8-working-directory>
              --python <base64-utf8-python-path>
              --conda-env <base64-utf8-env-name>
              --conda-prefix <base64-utf8-prefix>
              --arg <base64-utf8-script-argument>

            powershell-b64 options:
              --script <base64-utf8-script-body>
              --cwd <base64-utf8-working-directory>
              --exe <base64-utf8-powershell-path-or-command>

            wsl-b64 options:
              --file <base64-utf8-linux-path>
              --cwd <base64-utf8-linux-working-directory>
              --distribution <base64-utf8-distro-name>
              --user <base64-utf8-linux-user>
              --arg <base64-utf8-argument>

            wsl-capture-b64 options:
              --file <base64-utf8-linux-path>
              --cwd <base64-utf8-linux-working-directory>
              --distribution <base64-utf8-distro-name>
              --user <base64-utf8-linux-user>
              --arg <base64-utf8-argument>

            wsl-script-b64 options:
              --script <base64-utf8-shell-script-body>
              --shell <base64-utf8-linux-shell-path>
              --cwd <base64-utf8-linux-working-directory>
              --distribution <base64-utf8-distro-name>
              --user <base64-utf8-linux-user>
              --arg <base64-utf8-script-argument>

            wsl-script-capture-b64 options:
              --script <base64-utf8-shell-script-body>
              --shell <base64-utf8-linux-shell-path>
              --cwd <base64-utf8-linux-working-directory>
              --distribution <base64-utf8-distro-name>
              --user <base64-utf8-linux-user>
              --arg <base64-utf8-script-argument>

            everything-b64 options:
              --query <base64-utf8-query>
              --max <count>

            security option:
              --access-token <base64-utf8-token>
            """);
    }
}
