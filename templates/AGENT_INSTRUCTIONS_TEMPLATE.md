# WRE v6 Agent Instructions Template

Adapt this block for an agent operating a Windows host. Supply the released
controller checkout path and target alias; keep credentials in the private
target env file.

```markdown
Use WRE v6 for Windows and WSL operations.

Controller checkout: /absolute/path/wre-controller-6.1.2
Target alias: WIN-LAB
Host entry: C:/WRE/wre

Read the repository's deployment guide and the v6 reference at tag v6.1.2.
Start with `python3 -m v6.controller.shell WIN-LAB --info` from the checkout
root, then inspect with `--probe` and `--guard`.

Prefer the structured WRE MCP tools for routine operations. The CLI is
`python3 -m v6.controller.shell WIN-LAB <action> '<payload-json>'`.
Discover actual tool signatures and action schemas through `system.help`.

Use absolute Windows executable paths and argument arrays for process calls.
Use forward-slash Windows paths inside JSON. Stage script bodies with
`file.writeText`, then run the file by path. Track finite asynchronous jobs
through `process.start/status/wait/kill`.

RPC, file operations, processes and WSL run as the SSH user. Privileged work
uses `host.task.*` or the installed SYSTEM apply-agent. Treat the access token
as a SYSTEM credential; keep it and the SSH private key out of Git. Preserve
unrelated tasks and workloads.

The controller carries JSON on SSH stdin. If a read-phase transport error
follows a write, inspect host/job/task state before deliberately retrying.
Keep token enforcement and private-listener policy intact.

Deploy only a checksum-verified release package with a matching controller
tag. On-host installation needs explicit `-Name` and `-Listen`, including
when using `-KeepPolicy`. Controller updates need the extracted package's
`wre/` path passed through `--build-dir`. Follow the deployment guide for
backup, rollback, MCP setup and verification.

Report observed results, changes and remaining gaps. Distinguish command or
schema checks from a real Windows installation test.
```

[Deployment guide](../DEPLOYMENT.md) · [Quick Start](../QUICKSTART.md)
