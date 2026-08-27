# WRE v6.1.0 — New Machine Bootstrap Package

Point release of the [windows-remote-executor](../../) v6.1 tree (commit
`b305191`, tag `v6.1.0`), packaged for bringing a NEW Windows host under
WRE control. Everything runs from the package's own embedded Python —
the target machine needs nothing preinstalled.

## Package

| File | SHA256 |
|---|---|
| `wre-6.1.0-windows-x64.zip` (18,241,827 B) | `397be2f0c9a9dfe34cdf7f496d45cae035cc9dfb54f9009cc71e9c493a06a229` |

Supply chain: Python 3.12.10 embeddable + pywin32 308 + comtypes 1.4.8,
each pinned by sha256 in `v6/scripts/make_bootstrap_package.py` and
re-verified at build time (see in-zip `manifest.json`).

## What's inside

```
deploy-wre.py          elevated on-host installer (repair tasks, apply agent)
wre/rpc.py             the bridge entrypoint (rpc-stdio loop)
wre/native sources     actions/ win32/ controller-shared schemas
wre/python/            standalone CPython 3.12 + wheels, preinstalled
```

## New-machine setup (operator steps)

1. **Windows host prerequisites**: Administrator PowerShell; OpenSSH Server
   feature available (`Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0`
   if absent); know the host's tailscale/LAN IP.
2. Copy the zip to the host (SMB/USB/`scp`), expand:
   ```powershell
   Expand-Archive .\wre-6.1.0-windows-x64.zip -DestinationPath C:\WRE\pkg
   ```
3. **Elevated install** (creates `C:/WRE/wre` tree, sshd self-repair tasks,
   SYSTEM apply-agent; token source required — exactly one of):
   ```powershell
   & C:\WRE\pkg\wre\python\python.exe C:\WRE\pkg\deploy-wre.py `
       --target-name <NAME> --expected-listen <HOST-IP> `
       --access-token <plain-token>
   # or: --keep-existing-policy  (when re-installing over an existing tree)
   ```
   `<plain-token>` is any long random secret you generate; only its sha256 is
   stored on the host (`access-policy.json`). Record it for the controller side.
4. **Controller side** (macOS/Linux), add a target env file
   `windows-remote-executor/targets/<NAME>.env` (copy `example.env`; fields:
   SSH host/user/key/port + `TARGET_ACCESS_TOKEN` = same plain token).
5. Light deploy / update later needs NO elevation:
   ```bash
   PYTHONPATH=. python3 -m v6.scripts.deploy_sftp <NAME>
   PYTHONPATH=. python3 -m v6.scripts.verify_v6_remote <NAME>
   ```
6. Smoke test:
   ```bash
   PYTHONPATH=. python3 -m v6.controller.shell <NAME> --info
   ```

Full protocol/action reference: [`v6/V6.md`](../../v6/V6.md).

## Security model (read before exposing anything)

- Holding the access token == holding SYSTEM on the host (token holders can
  trigger the SYSTEM apply-agent / repair lane). Keep tokens off git; the
  repo never stores them and deploys abort rather than ship a null-token policy.
- The bridge has NO resident daemon: sshd remains the only door; every RPC
  call is one SSH session answering JSON lines.
