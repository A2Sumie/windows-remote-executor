# Windows Remote Executor MCP

This directory contains a minimal stdio MCP server for `win-remote`:

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

That means the model calls a tool like `win_run` instead of generating:

```bash
./windows-remote-executor/bin/win-remote run X570 ...
```

This removes day-to-day quoting drift from the model layer. The C# native executor owns process launch and structured argv handling; MCP owns JSON-shaped tool calls; the shell wrapper is only a compatibility and staging layer.

## Run

```bash
python3 ./windows-remote-executor/mcp/win_remote_mcp.py
```

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

## PowerShell stance

- `win_run` and `win_capture` still inherit the wrapper guardrails.
- Raw `powershell.exe` / `pwsh` transport is blocked by default there.
- If PowerShell or cmd script control is truly required, use `win_exec` / `win_exec_capture`; the PowerShell-specific tools remain compatibility aliases.

## WSL stance

- Prefer `win_wsl` and `win_wsl_script` over composing `wsl.exe ... bash -lc ...`.
- Prefer `win_wsl_py` / `win_wsl_py_capture` for Python inside WSL venv/conda/model environments. Pass `python`, `cwd`, `module` or `script_path`, and `script_args` as fields.
- `win_wsl_script` now goes through the wrapper's staged-file path, so it does not depend on expanding the whole script body into the Windows command line.
- `win_wsl`, `win_wsl_capture`, `win_wsl_script`, and `win_wsl_script_capture` accept `heartbeat_seconds` for long quiet foreground work.
- Prefer `win_wsl_resident` when the goal is a durable WSL-side service. It returns structured readiness diagnostics instead of only reporting that the launch command exited 0.
- Keep long-lived models, caches, and WSL virtualenvs on ext4 paths such as `/home/...`; use `win_wsl` only to bridge into that Linux-native tree.
- Avoid nested activation strings such as `bash -lc 'source ... && python -c ...'` when the WSL Python tools can carry the operation as structured JSON.

## Required client stance

For agent clients, use this MCP server over shelling out to `win-remote` directly whenever the tool is available. Shell remains useful for manual debugging, deployment, and compatibility, but MCP should be the default control plane for routine automation.

When a workflow cannot be represented by the existing MCP tools, add a native/MCP capability instead of teaching agents another quoting pattern. The purpose of this executor is to make spaces, quotes, Unicode, long scripts, and WSL arguments reliable by construction.
