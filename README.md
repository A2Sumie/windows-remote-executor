# Windows Remote Executor (WRE)

WRE lets macOS/Linux controllers and coding agents operate Windows hosts
through structured JSON RPC over SSH. The Windows bootstrap package includes
its Python runtime, while the controller provides a CLI, reusable sessions
and an optional stdio MCP server.

**Stable release: [v6.1.2](https://github.com/A2Sumie/windows-remote-executor/releases/tag/v6.1.2).**
For deployment, use that tag's controller source together with its Windows
package so both sides match.

## Get started

- [Deployment guide](DEPLOYMENT.md): Tailscale discovery, pwsh 7 initialization,
  saved controller credentials, SSH keys, installation, updates, rollback and MCP.
- [Onboarding helpers](onboarding/README.md): reusable pwsh 7 and hash-only
  Windows initialization scripts, also attached to the Release with checksums.
- [Quick Start](QUICKSTART.md): first commands and the action families.
- [v6.1.2 release notes](releases/v6.1.2/README.md): downloads and changes.
- [v6 protocol and actions](https://github.com/A2Sumie/windows-remote-executor/blob/v6.1.2/v6/V6.md).
- [Agent instructions template](templates/AGENT_INSTRUCTIONS_TEMPLATE.md).

## What WRE provides

- Synchronous process execution with structured output and exit codes.
- Supervised asynchronous jobs with status, output, wait and kill operations.
- File reads/writes, transfers and bounded filesystem search.
- Scheduled-task inspection and explicit privileged task lanes.
- WSL execution through structured arguments.
- A session mode that shares one SSH connection across multiple requests.
- Token authentication, listener-policy checks and on-host repair tasks.

The normal host entry is `C:/WRE/wre/rpc.py`, using the bundled interpreter.
RPC runs as the SSH login user; the elevated installer registers SYSTEM repair
and apply-agent tasks. Treat the access token as a SYSTEM credential and keep
it in a private, gitignored target env file.

## Source and packaging

The maintained control plane is `v6/`. Deployment source is fixed by the
chosen release tag; `v4/` and `v5/` are historical trees. Older v3/.NET release
assets remain available for historical versions.

The v6 Windows asset is `wre-6.1.2-windows-x64.zip`. It contains
`install-wre.ps1`, `deploy-wre.py`, the native RPC source and embedded Python.
GitHub's automatic source archives provide the controller/source checkout.
Build details and dependency SHA256 pins are in
[`v6/scripts/make_bootstrap_package.py`](https://github.com/A2Sumie/windows-remote-executor/blob/v6.1.2/v6/scripts/make_bootstrap_package.py).

Agent contributors should read [AGENTS.md](AGENTS.md). In the parent `livestr`
workspace, scope Git commands to this owning repository.

## License and provenance

[MIT](LICENSE). This standalone repository exports the executor toolkit;
source, documentation and packaging were developed through an agent workflow.
