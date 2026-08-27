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

> A fresh Windows box has **no** `C:\WRE` and a standard user cannot create it.
> Extract somewhere user-writable first (e.g. Downloads); elevate only for the
> install step — `deploy-wre.py` creates `C:\WRE\wre` itself (`mkdir -p`).

1. **Download & extract (normal, non-elevated shell):**
   ```powershell
   Expand-Archive .\wre-6.1.0-windows-x64.zip -DestinationPath "$env:USERPROFILE\Downloads\wre-pkg"
   ```
2. **Elevated install** (Right-click PowerShell → Run as administrator):
   ```powershell
   & "$env:USERPROFILE\Downloads\wre-pkg\deploy-wre.py" `
       --target-name <NAME> --expected-listen <HOST-IP> `
       --access-token <random-long-secret>
   # or: --keep-existing-policy  (when re-installing over an existing tree)
   ```
   `<random-long-secret>` is any long random string you generate; only its
   sha256 is stored on the host (`access-policy.json`). Record it for the
   controller side. The installer selftests the package, copies the tree to
   `C:\WRE\wre` (creating it), writes the policy, pins sshd_config ListenAddress
   (skips gracefully when OpenSSH Server isn't set up yet) and registers the
   sshd repair tasks + SYSTEM apply-agent.

   If OpenSSH Server is not installed yet:
   `Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0` then `Start-Service sshd`.
3. **Controller side** (macOS/Linux), add a target env file
   `windows-remote-executor/targets/<NAME>.env` (copy `example.env`; fields:
   SSH host/user/key/port + `TARGET_ACCESS_TOKEN` = same plain token).
4. Light deploy / update later needs NO elevation:
   ```bash
   PYTHONPATH=. python3 -m v6.scripts.deploy_sftp <NAME>
   PYTHONPATH=. python3 -m v6.scripts.verify_v6_remote <NAME>
   ```
5. Smoke test:
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
