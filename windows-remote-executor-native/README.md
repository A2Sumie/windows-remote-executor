# Windows Remote Executor Native

This is the Windows `.exe` companion for `windows-remote-executor/`. It exists so Codex-style tools can land one explicit executable on the Windows side and then prefer file transfer plus native V3 RPC over direct PowerShell remoting.

## Commands

The current native CLI exposes only installation, maintenance, and V3 stdio entrypoints:

- `bootstrap`
- `guard-sshd`
- `repair-sshd`
- `selftest`
- `rpc-selftest`
- `rpc-stdio`

`bootstrap` installs or verifies OpenSSH Server, writes `sshd_config`, narrows listening to the selected local IP, writes authorized keys, removes old Startup-folder/cmd recovery artifacts, installs launcher-based `repair-sshd` scheduled tasks for logon/startup/watch recovery, configures service startup and recovery actions, and starts `sshd`.

`guard-sshd` reads `access-policy.json`, checks configured and active `sshd` listeners, and disables the service when the host is in an unsafe state.

`repair-sshd` revalidates `sshd`, rewrites a known-good managed `sshd_config` when needed, regenerates host keys, reapplies scoped firewall and service settings, and brings the service back to `Running`.

`selftest` and `rpc-selftest` validate V3 request serialization, hostile payload preservation, drive-relative path rejection, and the supported action list without touching Windows state.

`rpc-stdio` is the remote-control entrypoint. It reads one UTF-8 JSON object from stdin and writes one UTF-8 JSON object to stdout. The authoritative action list is returned by the `host.capabilities` RPC action.

## V3 RPC Surface

Current V3 actions:

- `host.capabilities`
- `host.probe`
- `host.guard`
- `host.repair`
- `host.tasks`
- `host.policy`
- `process.run`
- `process.capture`
- `process.spawn`
- `script.run`
- `script.capture`
- `python.run`
- `wsl.run`
- `wsl.capture`
- `wsl.script`
- `wsl.script.capture`
- `wsl.resident`
- `file.writeText`
- `file.readText`
- `file.mkdir`
- `file.deleteTree`
- `file.copy`
- `everything.search`

See `../windows-remote-executor/V3.md` for request and response shapes.

## Access Policy

`access-policy.json` is expected next to the executable. It contains:

- `expectedListenAddress`
- `exposureMode`
- `commandMode`
- `label`
- `accessTokenSha256`
- `updatedAt`

If `accessTokenSha256` is present, V3 RPC actions require the matching token where the corresponding operation is protected by policy. `public-with-token` is only valid when a token hash exists. The intended default is still `private-only`.

`commandMode=argv-only` blocks shell/interpreter executables through `process.run`, `process.capture`, and `process.spawn`; V3 script actions remain available for staged maintenance.

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

This produces `windows-remote-executor-native/publish/scd-win-x64/WindowsRemoteExecutor.Native.exe`. It is convenient for brand-new-host bootstrap and drop-and-run deployment but more likely to trigger generic `.NET packer/compression` heuristics because the runtime is embedded in the executable.

`publish-win-x64.sh` is kept as a compatibility wrapper and currently points at the self-contained publish path.

Production host updates should consume GitHub release assets, not local publish directories.

## Usage On Windows

Run from an elevated shell:

```powershell
.\WindowsRemoteExecutor.Native.exe bootstrap `
  --public-key-file C:\Users\you\.ssh\id_ed25519.pub `
  --user Administrator `
  --listen-address 100.101.102.103

.\WindowsRemoteExecutor.Native.exe guard-sshd --expected-listen-address 100.101.102.103
.\WindowsRemoteExecutor.Native.exe repair-sshd --expected-listen-address 100.101.102.103
.\WindowsRemoteExecutor.Native.exe rpc-selftest
```

If you need to revert a host that was already switched to a PowerShell login shell:

```powershell
.\WindowsRemoteExecutor.Native.exe bootstrap --clear-default-shell
```

## Notes

- The intended steady state is "PowerShell minimized", not "PowerShell everywhere".
- PowerShell is still available through V3 `script.run` / `script.capture` when script-shaped maintenance is the lower-error route.
- Raw `powershell.exe`, `pwsh`, and hand-rolled `-EncodedCommand` transport are outside the normal supported path.
- The stable remote tool directory is `C:\CodexRemote\tools\`.
- `WindowsRemoteExecutor.cmd` is the stable launcher path; versioned native payloads can live under `C:\CodexRemote\tools\releases\...`.
- `guard-sshd` is designed for scheduled-task use as well as one-shot validation.
- Bootstrap installs headless repair tasks: `CodexRemote Sshd Repair Logon`, `CodexRemote Sshd Repair Startup`, and `CodexRemote Sshd Repair Watch`.
- Those tasks invoke the stable launcher for `repair-sshd`, so recovery no longer depends on visible console windows and hot updates do not need to overwrite an in-use `.exe`.
- `sshd` also gets Windows service recovery actions plus scheduled repair watch tasks so a later service stop is less likely to strand the host.
- Everything search depends on the SDK DLL being present next to the executable and on the Everything service being installed on the host.
