# Windows Remote Executor MCP

This directory contains a minimal stdio MCP server for `win-remote`:

```text
windows-remote-executor/mcp/win_remote_mcp.py
```

## Why this exists

The long-term quoting fix is to stop asking the model to compose shell and PowerShell command lines in the first place.

With MCP, the client sends structured JSON arguments such as:

- target
- program
- args
- cwd

That means the model calls a tool like `win_run` instead of generating:

```bash
./windows-remote-executor/bin/win-remote run X570 ...
```

This removes most day-to-day quoting drift from the model layer.

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
- `win_wsl_script`
- `win_wsl_script_capture`
- `win_put`
- `win_get`
- `win_guard`
- `win_repair`
- `win_tasks`
- `win_exec_ps_file`
- `win_exec_ps_script`

## PowerShell stance

- `win_run` and `win_capture` still inherit the wrapper guardrails.
- Raw `powershell.exe` / `pwsh` transport is blocked by default there.
- If PowerShell is truly required, use `win_exec_ps_file` or `win_exec_ps_script`.

## WSL stance

- Prefer `win_wsl` and `win_wsl_script` over composing `wsl.exe ... bash -lc ...`.
- `win_wsl_script` now goes through the wrapper's staged-file path, so it does not depend on expanding the whole script body into the Windows command line.
- Keep long-lived models, caches, and WSL virtualenvs on ext4 paths such as `/home/...`; use `win_wsl` only to bridge into that Linux-native tree.

## Recommended client stance

For agent clients, prefer this MCP server over shelling out to `win-remote` directly. Shell remains useful for manual debugging, but MCP should be the default control plane for routine automation.
