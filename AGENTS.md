# Agent Notes

This repository contains an executor-only toolkit for driving Windows hosts from macOS or Linux agent workflows.

## Repo Layout

- `windows-remote-executor/` is the local shell wrapper.
- `windows-remote-executor-native/` is the Windows-side native executor.
- `.github/workflows/release.yml` builds release artifacts from tags.

## Operating Intent

- Prefer `cmd.exe`, native process launch, file transfer, and Python execution.
- Treat PowerShell as a fallback for Windows-specific administration only.
- Prefer the framework-dependent `.NET 8` build unless a self-contained drop is explicitly required.
- Keep SSH scoped to a private address by default.
- If `access-policy.json` requires a token, always send it through the wrapper instead of invoking the native executable bare.

## Safe Defaults

- Do not add real hostnames, Tailscale IPs, usernames, SSH public keys, or access tokens to tracked files.
- Do not commit `targets/*.env` except `example.env`.
- Do not commit publish output under `windows-remote-executor-native/publish/`.
- For release work, let GitHub Actions build release assets from a tag push.

## Common Commands

```bash
dotnet build windows-remote-executor-native/src/WindowsRemoteExecutor.Native/WindowsRemoteExecutor.Native.csproj
./windows-remote-executor-native/publish-fdd-win-x64.sh
./windows-remote-executor-native/publish-scd-win-x64.sh
./windows-remote-executor/bin/win-remote probe <target>
./windows-remote-executor/bin/win-remote policy <target>
./windows-remote-executor/bin/win-remote guard <target>
./windows-remote-executor/bin/win-remote update-tools <target>
```

## Reusable Prompt Template

Use [templates/AGENT_INSTRUCTIONS_TEMPLATE.md](/Users/zou/ytdlp/subPrep/livestr/windows-remote-executor-public/templates/AGENT_INSTRUCTIONS_TEMPLATE.md) when you want to hand this executor to Codex, Claude Code, or another agent and want a ready-made operating brief.
