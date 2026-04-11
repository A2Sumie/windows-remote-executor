# Windows Remote Executor Native

This is the Windows `.exe` companion for `windows-remote-executor/`. It exists so Codex-style tools can land one explicit executable on the Windows side and then prefer file transfer plus native process launch over direct PowerShell remoting.

## Commands

The current native CLI exposes:

- `bootstrap`
- `bootstrap-x570` as a legacy alias
- `guard-sshd`
- `repair-sshd`
- `probe`
- `run-b64`
- `capture-b64`
- `python-b64`
- `powershell-b64`
- `wsl-b64`
- `wsl-capture-b64`
- `wsl-script-b64`
- `wsl-script-capture-b64`
- `everything-b64`

`bootstrap` installs or verifies OpenSSH Server, writes `sshd_config`, narrows listening to the selected local IP, writes authorized keys, removes any legacy `cmd` recovery artifacts, installs launcher-based `repair-sshd` scheduled tasks for logon/startup/watch recovery, configures service startup and recovery actions, and starts `sshd`.

`guard-sshd` reads `access-policy.json`, checks configured and active `sshd` listeners, and disables the service when the host is in an unsafe state.

`repair-sshd` revalidates `sshd`, rewrites a known-good managed `sshd_config` when needed, regenerates host keys, reapplies scoped firewall and service settings, and brings the service back to `Running`.

`probe` returns machine state plus the active exposure policy label, listen addresses, and whether an access token is required.

`run-b64`, `python-b64`, `powershell-b64`, and the WSL commands execute payloads without depending on local shell quoting on the controlling machine.

`wsl-b64` and `wsl-capture-b64` run Linux programs through `wsl.exe --exec` with structured distro/user/cwd arguments.

`wsl-script-b64` and `wsl-script-capture-b64` write a temporary shell script on the Windows side, translate the path into WSL form, and run it through a Linux shell without requiring `bash -lc` string composition. The higher-level `win-remote wsl-sh` wrapper now stages longer local scripts through file transfer first so it does not have to expand the whole payload into the Windows command line.

`capture-b64` executes a native process and prints one JSON object with:

- `exitCode`
- `stdoutText`
- `stderrText`
- `stdoutEncoding`
- `stderrEncoding`
- `stdoutBase64`
- `stderrBase64`
- `stdoutBytes`
- `stderrBytes`

Use it when output may be UTF-16, locale-codepage, or byte-sensitive and you want one machine-readable payload instead of best-effort live text.

## Access Policy

`access-policy.json` is expected next to the executable. It contains:

- `expectedListenAddress`
- `exposureMode`
- `label`
- `accessTokenSha256`
- `updatedAt`

If `accessTokenSha256` is present, the native executor requires a matching token for `probe`, `run-b64`, `capture-b64`, `python-b64`, `powershell-b64`, the WSL commands, and `everything-b64`.

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
.\WindowsRemoteExecutor.Native.exe repair-sshd --expected-listen-address 100.101.102.103
```

If you need to revert a host that was already switched to a PowerShell login shell:

```powershell
.\WindowsRemoteExecutor.Native.exe bootstrap --clear-default-shell
```

## Notes

- The intended steady state is "PowerShell minimized", not "PowerShell everywhere".
- PowerShell is still available, but it is expected to arrive only through the wrapper's UTF-8/base64 transport before PowerShell starts.
- Raw `powershell.exe`, `pwsh`, and hand-rolled `-EncodedCommand` transport are outside the supported path.
- On `X570`, `cmd.exe` is also outside the supported steady-state control path. Prefer direct native executables through `run-b64`.
- `run-b64` is a best-effort text path. `capture-b64` is the byte-preserving path when encoding is unclear.
- `capture-b64` is normally reached through `win-remote capture`, which handles UTF-8 base64 argument transport for you.
- `wsl-b64` is normally reached through `win-remote wsl`, and `wsl-script-b64` is normally reached through `win-remote wsl-sh`.
- The stable remote tool directory is `C:\CodexRemote\tools\`.
- `WindowsRemoteExecutor.cmd` is the stable launcher path; versioned native payloads can live under `C:\CodexRemote\tools\releases\...`.
- `guard-sshd` is designed for scheduled-task use as well as one-shot validation.
- Bootstrap installs three headless repair tasks: `CodexRemote Sshd Repair Logon`, `CodexRemote Sshd Repair Startup`, and `CodexRemote Sshd Repair Watch`.
- Those tasks invoke the stable launcher for `repair-sshd`, so recovery no longer depends on `cmd.exe` wrappers or visible console windows and hot updates do not need to overwrite an in-use `.exe`.
- `sshd` also gets Windows service recovery actions plus scheduled repair watch tasks so a later service stop is less likely to strand the host.
- Everything search still depends on the SDK DLL being present next to the executable and on the Everything service being installed on the host.
