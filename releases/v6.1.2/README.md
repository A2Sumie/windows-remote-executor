# WRE v6.1.2 — New Machine Bootstrap Package

Point release of the [windows-remote-executor](../../) v6.1 tree (tag
`v6.1.2`), packaged for bringing a NEW Windows host under WRE control.
Everything runs from the package's own embedded Python — the target machine
needs nothing preinstalled.

## Package

| File | SHA256 |
|---|---|
| `wre-6.1.2-windows-x64.zip` (18,247,093 B) | `677a562326aa0d9ffb2a679c8f3f529884767bdc10db516bd64e305a421952f7` |

Supply chain: Python 3.12.10 embeddable + pywin32 308 + comtypes 1.4.8,
each pinned by sha256 in `v6/scripts/make_bootstrap_package.py` and
re-verified at build time (see in-zip `manifest.json`).

## What's inside

```
deploy-wre.py          elevated on-host installer (repair tasks, apply agent)
install-wre.ps1        one-command self-elevating wrapper (any extract path)
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
   Expand-Archive .\wre-6.1.2-windows-x64.zip -DestinationPath "$env:USERPROFILE\Downloads\wre-pkg"
   ```
2. **One-command install** (the script self-elevates, checks sshd, validates args):
   ```powershell
   powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\Downloads\wre-pkg\install-wre.ps1" ^
       -Name <NAME> -Listen <HOST-IP> -Token <random-long-secret>
   # re-install over an existing tree without retyping the token:
   powershell -ExecutionPolicy Bypass -File ...\install-wre.ps1 -KeepPolicy
   # custom install root:
   ...install-wre.ps1 -Name <NAME> -Dest D:\somewhere\wre -KeepPolicy
   ```
   Equivalent manual path (what the wrapper drives):
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

   If OpenSSH Server is not installed yet, `install-wre.ps1` offers to run:
   `Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0` then starts sshd.
3. **Controller side** (macOS/Linux), add a target env file
   `v6/targets/<NAME>.env` (copy `v6/targets/example.env`; fields:
   SSH host/user/key/port + `TARGET_ACCESS_TOKEN` = same plain token).
   Real target env files are gitignored — never commit tokens.
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

## Note on `wre_find` (wreq index search)

The MCP `wre_find`/`wre_find_check`/`wre_find_catchup` tools drive
`C:/WRE/tmp/wreq.exe` on the host, which is deployed **separately** and is
not part of this bootstrap package. Without it, `wre_find` falls back to the
bounded `wre_search` walk.
