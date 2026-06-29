# Windows Remote Executor MCP

This directory contains a minimal stdio MCP server for Windows Remote Executor V3:

```text
windows-remote-executor/mcp/win_remote_mcp.py
```

## Why this exists

The quoting fix is to stop asking the model to compose shell, `cmd.exe`, PowerShell, or `wsl.exe ... bash -lc ...` command lines in the first place.

With MCP, the client sends structured JSON arguments such as:

- target
- program
- args
- cwd
- script body
- WSL distribution/user/shell fields

That means the model calls a tool like `win_run` instead of generating a shell command string. The MCP handler calls `windows-remote-executor/lib/wre_v3_client.py` directly, and that client sends one JSON request line to native `rpc-stdio`.

V3 keeps the SSH command fixed as:

```text
WindowsRemoteExecutor.Native.exe rpc-stdio
```

User payload travels on stdin JSON. It does not travel in local argv, remote argv, PowerShell command strings, or `cmd.exe` command strings.

## Run

```bash
python3 ./windows-remote-executor/mcp/win_remote_mcp.py
```

There is no MCP transport environment switch. All MCP tools use V3.

## Exposed tools

- `win_probe`
- `win_run`
- `win_capture`
- `win_py`
- `win_wsl`
- `win_wsl_capture`
- `win_wsl_py`
- `win_wsl_py_capture`
- `win_wsl_script`
- `win_wsl_script_capture`
- `win_wsl_resident`
- `win_put`
- `win_get`
- `win_guard`
- `win_repair`
- `win_tasks`
- `win_exec`
- `win_exec_capture`
- `win_exec_ps_file`
- `win_exec_ps_script`

## Tool routing

- `win_probe` -> `host.probe`
- `win_run` -> `process.run`
- `win_capture` -> `process.capture`
- `win_py` -> `python.run`
- `win_wsl` -> `wsl.run`
- `win_wsl_capture` -> `wsl.capture`
- `win_wsl_script` -> `wsl.script`
- `win_wsl_script_capture` -> `wsl.script.capture`
- `win_wsl_resident` -> `wsl.resident`
- `win_put` -> `file.mkdir` plus `scp` upload
- `win_get` -> `scp` download
- `win_guard` -> `host.guard`
- `win_repair` -> `host.repair`
- `win_tasks` -> `host.tasks`
- `win_exec` -> `script.run`
- `win_exec_capture` -> `script.capture`

`scp` remains the byte-transfer lane for file upload/download. V3 RPC owns the remote control plane around those transfers, such as creating directories and running post scripts.

## PowerShell stance

- `win_run` and `win_capture` inherit the raw PowerShell guardrail.
- Raw `powershell.exe` / `pwsh` through argv routes is blocked unless the caller explicitly enables the escape hatch.
- If PowerShell or cmd script control is the lower-error route, use `win_exec` / `win_exec_capture`; the PowerShell-specific tools are convenience aliases over the same V3 script actions.
- Remote Windows paths should be passed with forward slashes or as quoted backslash strings. Drive-relative shapes such as `D:folderfile.py` are rejected before remote execution.

## WSL stance

- Prefer `win_wsl` and `win_wsl_script` over composing `wsl.exe ... bash -lc ...`.
- Prefer `win_wsl_py` / `win_wsl_py_capture` for Python inside WSL venv/conda/model environments. Pass `python`, `cwd`, `module` or `script_path`, and `script_args` as fields.
- `win_wsl_script` sends the script body as V3 JSON and lets the native executor stage it into WSL temp space.
- Prefer `win_wsl_resident` when the goal is a durable WSL-side service. It returns structured readiness diagnostics instead of only reporting that the launch command exited 0.
- Keep long-lived models, caches, and WSL virtualenvs on ext4 paths such as `/home/...`; use the executor only to bridge into that Linux-native tree.
- Avoid nested activation strings such as `bash -lc 'source ... && python -c ...'` when the WSL Python tools can carry the operation as structured JSON.

## Required client stance

For agent clients, use this MCP server over shelling out to `win-remote` directly whenever the tool is available. The shell shim remains useful for manual debugging, release deployment, and bootstrap packaging, but MCP should be the default control plane for routine automation.

When a workflow cannot be represented by the existing MCP tools, add a V3 RPC action plus MCP capability instead of teaching agents another quoting pattern. The purpose of this executor is to make spaces, quotes, Unicode, long scripts, and WSL arguments reliable by construction.
