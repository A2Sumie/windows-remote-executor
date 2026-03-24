# Windows Remote Executor Native

This is the Windows `.exe` companion for `windows-remote-executor/`. It exists so Codex-style tools can land one explicit executable on the Windows side and then prefer file transfer plus native process launch over direct PowerShell remoting.

## Commands

The current native CLI exposes:

- `bootstrap`
- `bootstrap-x570` as a legacy alias
- `guard-sshd`
- `probe`
- `run-b64`
- `python-b64`
- `powershell-b64`
- `everything-b64`

`bootstrap` installs or verifies OpenSSH Server, writes `sshd_config`, narrows listening to the selected local IP, writes authorized keys, creates a visible `cmd.exe` startup console for the target user, configures service startup, and starts `sshd`.

`guard-sshd` reads `access-policy.json`, checks configured and active `sshd` listeners, and disables the service when the host is in an unsafe state.

`probe` returns machine state plus the active exposure policy label, listen addresses, and whether an access token is required.

`run-b64`, `python-b64`, and `powershell-b64` execute payloads without depending on local shell quoting on the controlling machine.

## Access Policy

`access-policy.json` is expected next to the executable. It contains:

- `expectedListenAddress`
- `exposureMode`
- `label`
- `accessTokenSha256`
- `updatedAt`

If `accessTokenSha256` is present, the native executor requires a matching token for `probe`, `run-b64`, `python-b64`, `powershell-b64`, and `everything-b64`.

`public-with-token` is only valid when a token hash exists. The intended default is still `private-only`.

## Build

Debug build:

```bash
dotnet build windows-remote-executor-native/src/WindowsRemoteExecutor.Native/WindowsRemoteExecutor.Native.csproj
```

Preferred publish for source review and GitHub releases:

```bash
./windows-remote-executor-native/publish-fdd-win-x64.sh
```

This produces a framework-dependent Windows publish under `windows-remote-executor-native/publish/fdd-win-x64`. It is smaller and avoids bundling the .NET runtime into one file, which usually makes AV and VirusTotal results easier to interpret.

The current project target is `.NET 8` on Windows, so the framework-dependent build expects a compatible `Microsoft.NETCore.App 8.x` runtime on the host.

Optional self-contained single-file publish:

```bash
./windows-remote-executor-native/publish-scd-win-x64.sh
```

This produces `windows-remote-executor-native/publish/scd-win-x64/WindowsRemoteExecutor.Native.exe`. It is convenient for drop-and-run deployment but more likely to trigger generic `.NET packer/compression` heuristics because the runtime is embedded in the executable.

`publish-win-x64.sh` is kept as a compatibility wrapper and currently points at the self-contained publish path.

## Usage On Windows

Run from an elevated shell:

```powershell
.\WindowsRemoteExecutor.Native.exe bootstrap `
  --public-key-file C:\Users\you\.ssh\id_ed25519.pub `
  --user Administrator `
  --listen-address 100.101.102.103

.\WindowsRemoteExecutor.Native.exe probe
.\WindowsRemoteExecutor.Native.exe guard-sshd --expected-listen-address 100.101.102.103
```

If you need to revert a host that was already switched to a PowerShell login shell:

```powershell
.\WindowsRemoteExecutor.Native.exe bootstrap --clear-default-shell
```

## Notes

- The intended steady state is "PowerShell minimized", not "PowerShell everywhere".
- PowerShell is still available, but the wrapper now sends UTF-8 base64 bodies that are decoded on Windows before PowerShell starts.
- The stable remote tool directory is `C:\CodexRemote\tools\`.
- `guard-sshd` is designed for scheduled-task use as well as one-shot validation.
- Everything search still depends on the SDK DLL being present next to the executable and on the Everything service being installed on the host.
