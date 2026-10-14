# WRE v6 Quick Start

For a new Windows host, follow [DEPLOYMENT.md](DEPLOYMENT.md) first. It prepares
OpenSSH, installs the Windows package and sets up a matching controller.

## Prepare the controller

On macOS/Linux with Python 3.10+ and OpenSSH client tools:

```bash
git clone --branch v6.1.2 --depth 1 https://github.com/A2Sumie/windows-remote-executor.git wre-controller-6.1.2
cd wre-controller-6.1.2
cp v6/targets/example.env v6/targets/WIN-LAB.env
chmod 600 v6/targets/WIN-LAB.env
```

Set the real SSH host/user/port, private-key path,
`TARGET_EXPECTED_LISTEN_ADDRESS` and `TARGET_ACCESS_TOKEN` in that env file.
The token is the plain secret used by the Windows installer. SSH key
authentication must work unattended (`BatchMode=yes`).

## First commands

Run from the released controller checkout root:

```bash
PYTHONPATH=. python3 -m v6.controller.shell WIN-LAB --info
PYTHONPATH=. python3 -m v6.controller.shell WIN-LAB --probe
PYTHONPATH=. python3 -m v6.controller.shell WIN-LAB --guard
PYTHONPATH=. python3 -m v6.controller.shell WIN-LAB process.run '{"exe":"C:/WRE/wre/python/python.exe","args":["--version"]}'
PYTHONPATH=. python3 -m v6.controller.shell WIN-LAB host.tasks.list '{"prefix":"WRE","limit":20}'
```

Expect `ok: true`, protocol 6, build `v6.1` and a valid policy. The process
result should have `exitCode: 0`. `--guard` inspects with `noDisable: true`.
For repeated calls, use a session or REPL:

```bash
PYTHONPATH=. python3 -m v6.controller.shell WIN-LAB --repl
PYTHONPATH=. python3 -m v6.controller.shell WIN-LAB --session host.info '{}' host.tasks.list '{"prefix":"WRE","limit":20}'
```

## Choose the action

| Work | v6 action/tool route |
|---|---|
| Run a concrete executable | `process.run` / MCP `wre_run`, absolute `exe` plus an `args` array |
| Start a finite job | `process.start`, then `process.status`, `process.wait` or `process.kill` |
| Read/write a file | `file.readText` / `file.writeText` |
| Search files | `file.search` / MCP `wre_search`; optional `wre_find` needs separate `wreq.exe` |
| Inspect tasks | `host.tasks.list` / `host.tasks.detail` |
| Privileged task work | `host.task.*` or the installed SYSTEM apply-agent |
| Run in WSL | `wsl.run` with an explicit argument vector |
| Discover a schema | `system.help` or `host.capabilities` |

Keep Windows paths in forward-slash form inside JSON. Send script bodies as
file text and run the staged file with explicit arguments. Processes and WSL
run as the SSH user; privileged tasks use the explicit SYSTEM lanes.
A transport break after a write may mean the host already processed it, so
inspect state before deliberately retrying.

For installation, release-based updates, rollback and MCP configuration, use
[the deployment guide](DEPLOYMENT.md). For the full schema, read
[the v6 reference at the release tag](https://github.com/A2Sumie/windows-remote-executor/blob/v6.1.2/v6/V6.md).
