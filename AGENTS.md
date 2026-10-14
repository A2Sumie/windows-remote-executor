# Agent Guide

Use the maintained WRE v6 control plane for Windows/WSL operations from
macOS/Linux. Start with [DEPLOYMENT.md](DEPLOYMENT.md) for installation and
[QUICKSTART.md](QUICKSTART.md) for entrypoint discovery.

## Release source of truth

- Stable deployment: tag `v6.1.2`, Windows asset
  `wre-6.1.2-windows-x64.zip`, matching controller checkout.
- Source: `v6/native/`, `v6/controller/`, `v6/scripts/` and
  `v6/mcp/wre_mcp.py` within the selected tag.
- Schema/reference: `v6/native/schemas.py` and `v6/V6.md` at that tag.
- `v4/` and `v5/` are frozen history. Historical v3 code/assets are outside
  the current release's deployment route.
- From the parent livestr workspace, scope Git to
  `git -C windows-remote-executor-public ...`.

Use a separate released controller checkout for deployment. Do not substitute
an unrelated working-tree build for a verified release asset.

## Target and entrypoint

Target env files in v6.1.2 live at `v6/targets/<NAME>.env`. Copy the released
`example.env`, fill SSH details and the plain `TARGET_ACCESS_TOKEN`, set mode
600, and keep real credentials out of Git. `WRE_TARGETS_DIR` can select a
private target directory.

Default host tree: `C:/WRE/wre`. The controller invokes the bundled
`python/python.exe -I -X utf8 rpc.py rpc-stdio` through SSH. Requests and
responses are JSON lines; operation payloads travel on stdin.

```bash
PYTHONPATH=. python3 -m v6.controller.shell WIN-LAB --info
PYTHONPATH=. python3 -m v6.controller.shell WIN-LAB --probe
PYTHONPATH=. python3 -m v6.controller.shell WIN-LAB host.tasks.list '{"prefix":"WRE","limit":20}'
```

## Operating rules

- Prefer the structured MCP frontend for agent operations; use the v6 CLI for
  manual inspection and diagnosis. Discover the actual tool signatures.
- Concrete process calls use an absolute `exe` and an `args` array. Stage
  scripts with `file.writeText` and pass their paths as arguments.
- Use PowerShell Core >=7 (`pwsh.exe`) for PowerShell scripts, passing
  `-NoLogo -NoProfile -NonInteractive -File` and a staged script path.
  Discover the actual executable per host; verify a machine-wide path for
  SYSTEM tasks rather than assuming a user WindowsApps alias works there.
- `process.*`, `file.*` and `wsl.*` run as the SSH login user. Privileged work
  uses `host.task.*` or the elevated-bootstrap SYSTEM apply-agent.
- Treat the token as SYSTEM-equivalent. Protected paths are an accident
  guardrail; `allowProtected` is an explicit override.
- Use `host.tasks.list`/`host.tasks.detail` for inspection and `system.help`
  for action schemas. Preserve unrelated host workloads.
- Use bounded `file.search`; the MCP `wre_find` family requires a separately
  deployed `C:/WRE/tmp/wreq.exe`.
- Transport read failures are not automatically resent. Inspect state before
  repeating a possibly completed write/job/task action.
- Missing/corrupt/null-token policy fails closed except for
  `host.capabilities`. Diagnose the v6 tree and policy through documented
  entrypoints.

## Deployment and verification

The assisted first installation runs locally in Administrator pwsh 7 using
the Release's `initialize-wre.ps1` and a controller-generated hash-only
bootstrap. `create-wre-target.py` retains the plain Token in a private env;
`--reuse` and `--rotate-token` are explicit credential choices. The original
native `install-wre.ps1` requires `-Name`, `-Listen`, and exactly one of
`-Token`/`-KeepPolicy`. Keep `-Name` and `-Listen` explicit on native reinstalls.
Align the policy/apply spec/listener together when the address changes.

For later controller uploads, use the verified release's extracted `wre/`
directory as `deploy_sftp --build-dir`, prepare `C:/WRE/inbox`, and keep
SSH/SFTP available. Detailed commands, backup and rollback are in
[DEPLOYMENT.md](DEPLOYMENT.md).

Verify through the delivered entrypoint: `--info`, `--probe`, `--guard`, a
process smoke and repair/apply task inspection. The full
`v6.scripts.verify_v6_remote` matrix also checks WSL; record the WSL gap on
hosts where that optional component is unavailable. Choose checks appropriate
to the change and distinguish static validation from a real Windows install.
