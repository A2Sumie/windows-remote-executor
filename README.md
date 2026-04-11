# Windows Remote Executor

Windows Remote Executor is a two-part toolkit for operating Windows hosts from macOS or Linux Codex-style agentic workflows.

- `windows-remote-executor/` is the local shell wrapper
- `windows-remote-executor-native/` is the Windows-side native executor

The design goal is simple: keep SSH as the transport, keep PowerShell as a reluctant fallback, and prefer a dropped native executable plus file transfer over brittle inline script transport. That reduces local quoting failures, keeps the control plane easier to reason about, and narrows the amount of PowerShell/AMSI-shaped surface used during normal automation.

For agentic clients, the preferred control plane is now the structured MCP server in `windows-remote-executor/MCP.md`, not ad hoc shell command generation.

## Features

- remote `cmd.exe`, native process, and Python execution
- structured capture for localized or byte-sensitive process output
- Windows-local PowerShell decode path for the cases where PowerShell is unavoidable
- structured WSL program and script execution so Linux-side work does not need `wsl.exe ... bash -lc ...`
- a minimal stdio MCP server so agents can call structured tools instead of composing shell strings
- structured scheduled-task inspection so task names with spaces do not need handwritten PowerShell quoting
- JSON host probing
- staged directory deploys
- hot updates for the remote tool directory that switch a stable launcher to a new versioned release
- private-network policy enforcement
- optional access-token requirement for native commands
- automatic `sshd` disablement when listener exposure drifts outside policy
- explicit `repair-sshd` / `win-remote repair` self-heal for config or startup drift

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

The current framework-dependent build targets `.NET 8` on Windows.

## Agent Template

This repository also ships agent-facing entrypoints that are meant to be discovered directly by tooling:

- `AGENTS.md` for repository-local agent guidance
- `CLAUDE.md` for Claude Code style entrypoint discovery
- `CODEX.md` for Codex style entrypoint discovery
- `templates/AGENT_INSTRUCTIONS_TEMPLATE.md` for copy-paste system-prompt or task-brief usage

## Agent Quick Start

If an agent opens this repository cold, the shortest safe path is:

1. Read `AGENTS.md`.
2. Read `windows-remote-executor/README.md`.
3. Run `./windows-remote-executor/bin/win-remote probe <target>`.
4. Prefer `run`, `capture`, `wsl`, `wsl-sh`, `py`, `put`, `get`, `deploy`, `policy`, `guard`, `repair`, `tasks`, and `update-tools`.
5. Prefer the MCP server for routine agent use; use `exec --file` only when PowerShell is actually needed.

## License

MIT.

## Provenance

This export was prepared as a standalone executor-only repository and intentionally excludes the rest of the workspace.

Source, docs, and packaging for this repo were produced end-to-end with Codex.
