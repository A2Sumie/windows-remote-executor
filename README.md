# Windows Remote Executor

Windows Remote Executor is a two-part toolkit for operating Windows hosts from macOS or Linux Codex-style agentic workflows.

- `windows-remote-executor/` is the local shell wrapper
- `windows-remote-executor-native/` is the Windows-side native executor

The design goal is simple: keep SSH as the transport, keep PowerShell as a reluctant fallback, and prefer a dropped native executable plus file transfer over brittle inline script transport. That reduces local quoting failures, keeps the control plane easier to reason about, and narrows the amount of PowerShell/AMSI-shaped surface used during normal automation.

## Features

- remote `cmd.exe`, native process, and Python execution
- Windows-local PowerShell decode path for the cases where PowerShell is unavoidable
- JSON host probing
- staged directory deploys
- hot updates for the remote tool directory
- private-network policy enforcement
- optional access-token requirement for native commands
- automatic `sshd` disablement when listener exposure drifts outside policy

## Security

- default mode is `private-only`
- private mode allows only standard private IPv4 ranges plus loopback and link-local recovery addresses
- wildcard listeners are treated as unsafe
- public exposure is opt-in only, requires an access token hash, and is explicitly labeled
- the guard can run manually or as a scheduled task

## Build and Release

The native project supports two Windows publish modes:

- framework-dependent: smaller, easier to inspect, better for GitHub/source releases
- self-contained single-file: easier to drop on a host, but more likely to trigger generic `.NET packer/compression` heuristics

Start with the framework-dependent publish unless you specifically need drop-and-run deployment.

## License

MIT.

## Provenance

This export was prepared as a standalone executor-only repository and intentionally excludes the rest of the workspace.

Source, docs, and packaging for this repo were produced end-to-end with Codex.
