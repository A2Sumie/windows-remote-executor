# Windows Remote Executor

Windows Remote Executor is a two-part toolkit for operating Windows hosts from macOS or Linux Codex-style agentic workflows.

- `windows-remote-executor/` is the local Python wrapper plus a small shell shim
- `windows-remote-executor-native/` is the Windows-side native executor

In the parent `livestr` workspace, the legacy top-level paths may be symlinks into this repository. Keep this as the single source tree; do not maintain a second copy.

The design goal is simple: keep SSH as the transport and choose routes that minimize quoting, encoding, path, and command-length failures. Updated targets receive routine operations as one `invoke-b64` JSON envelope into the native executor, so user command text travels as structured data rather than shell syntax. Older targets keep working through `win-remote-legacy` until they are updated from a release asset.

For agentic clients, the preferred control plane is now the structured MCP server in `windows-remote-executor/MCP.md`, not ad hoc shell command generation.

## Features

- remote native process and Python execution through a single `invoke-b64` envelope
- structured capture for localized or byte-sensitive process output
- staged PowerShell/cmd script bridge for cases that are not naturally argv-shaped
- structured WSL program and script execution so Linux-side work does not need `wsl.exe ... bash -lc ...`
- explicit WSL Python execution for venv/conda/model environments without nested shell activation strings
- staged WSL script transfer so longer shell payloads do not hit Windows command-line length limits
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

Remote executor deployments must use GitHub release assets. Local publish outputs are for development verification only; after changes are verified, tag the repository, let GitHub Actions build the release, and update the remote host from that release artifact.

## Agent Template

This repository also ships agent-facing entrypoints that are meant to be discovered directly by tooling:

- `AGENTS.md` for repository-local agent guidance
- `CODEX.md` for Codex style entrypoint discovery
- `templates/AGENT_INSTRUCTIONS_TEMPLATE.md` for copy-paste system-prompt or task-brief usage

## Agent Quick Start

If an agent opens this repository cold, the shortest safe path is:

1. Read `AGENTS.md`.
2. Read `windows-remote-executor/README.md`.
3. Run `./windows-remote-executor/bin/win-remote probe <target>`.
4. Prefer `run`, `capture`, `wsl`, `wsl-sh`, `py`, `put`, `get`, `deploy`, `policy`, `guard`, `repair`, `tasks`, `exec`, and `update-tools`; updated targets use the native `invoke-b64` envelope under the wrapper.
5. Prefer the MCP server for routine agent use; use `exec --file` only when script control is actually needed.
6. Keep long-lived WSL workloads on ext4 paths such as `/home/...`, not `/mnt/*`, and prefer `wsl-py-capture` / `wsl-capture` plus absolute interpreters for machine decisions.

## License

MIT.

## Provenance

This export was prepared as a standalone executor-only repository and intentionally excludes the rest of the workspace.

Source, docs, and packaging for this repo were produced end-to-end with Codex.
