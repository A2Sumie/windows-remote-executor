# WRE v6.1.2

Windows/WSL control through JSON RPC over SSH: processes and jobs, files,
scheduled tasks, WSL, sessions and an optional MCP frontend.

[Deployment guide](https://github.com/A2Sumie/windows-remote-executor/blob/main/DEPLOYMENT.md)
· [Quick Start](https://github.com/A2Sumie/windows-remote-executor/blob/main/QUICKSTART.md)
· [Onboarding helpers](https://github.com/A2Sumie/windows-remote-executor/tree/main/onboarding)
· [Protocol](https://github.com/A2Sumie/windows-remote-executor/blob/v6.1.2/v6/V6.md)

## Downloads

| Asset | Use |
|---|---|
| `wre-6.1.2-windows-x64.zip` | Windows runtime, native source and original installer |
| `SHA256SUMS.txt` | Original runtime ZIP checksum |
| `create-wre-target.py` | Controller credential storage and hash-only Windows bootstrap |
| `initialize-pwsh7.ps1` | pwsh 7 Terminal/parser/analyzer initialization |
| `initialize-wre.ps1` | Assisted Windows console installation and diagnostic reports |
| `ONBOARDING_SHA256SUMS.txt` | Checksums for the three onboarding helpers |
| `DEPLOYMENT.md` | Downloadable installation, update and troubleshooting guide |

Runtime ZIP SHA256:

```text
677a562326aa0d9ffb2a679c8f3f529884767bdc10db516bd64e305a421952f7
```

GitHub's Source code archives provide the controller/source checkout. Use
the named Windows ZIP for the bundled Python runtime.

## Installation flow updated 2026-10-14

1. Install or discover pwsh 7; initialize the Windows Terminal Core profile.
   Use a machine-wide MSI installation for SYSTEM tasks.
2. Use `create-wre-target.py` on the controller to discover the exact online
   Tailscale peer and save the private env (0600). The Token is never printed.
3. Transfer only its bootstrap JSON: public key, actual target/controller
   addresses and Token SHA256.
4. In local Administrator pwsh 7, run the checksum-verified
   `initialize-wre.ps1` with `-ConfigFile` and `-PackageDirectory`.
   Use `-CheckOnly` first.
5. Verify actual SSH/WRE authentication with the matching v6.1.2 controller,
   including a token-required RPC, process execution and exact active listener.

The initializer validates the assigned host address and package selftest,
uses NetSecurity for a tailnet-only SSH rule, authorizes the public key with
Windows SID-based ACLs, aligns policy/apply spec/listener, grants the selected
SSH user access to the WRE directory, and verifies the required SYSTEM tasks.
It preserves existing business apply-spec fields during reinstallation.

The plain Token and SSH private key stay on the controller. Existing targets
require explicit `--reuse` or `--rotate-token`; rotation saves a private backup.
Installer errors include a phase and a persistent JSON/transcript path.

For local parsing/lint, use
`pwsh -NoLogo -NoProfile -NonInteractive -File initialize-pwsh7.ps1 -InstallAnalyzer`.
WRE's control protocol uses the fixed bundled Python `rpc-stdio` entrypoint.

## Existing host and recovery

Use `--reuse` to regenerate a bootstrap from the saved private target, then
`initialize-wre.ps1 -PreserveToken` for a matching-token reinstall. A listener
change must update policy, apply spec and sshd configuration together.

For a lost Token, use `--rotate-token`, transfer the new bootstrap and run the
initializer without `-PreserveToken`. Complete controller verification before
considering the connection ready.

Default WRE executable paths for user-managed antivirus exceptions:

```text
C:/WRE/wre/python/python.exe
C:/WRE/wre/python/pythonw.exe
```

The guide also covers bootstrap executable exceptions, public-key failures,
stopped sshd, Taildrop filename locks, backups, rollback and MCP configuration.

## Verification and runtime notes

The onboarding controller test suite and syntax/PSScriptAnalyzer gates passed.
Real Windows Core 7.6.6 read-only package preflight and isolated Terminal/ACL/
apply-spec checks passed; macOS Core 7.6.0 parser/analyzer initialization also
passed. This update did not exercise a full fresh privileged Windows install.

The original v6.1.2 runtime ZIP retains its published checksum. Runtime build
is `v6.1`, protocol 6, bundled Python 3.12.10. Use the matching tag for the
controller. WSL is optional: the full verifier may report `wsl.list/not-found`
when it is absent. The `wre_find` family requires separately deployed
`C:/WRE/tmp/wreq.exe`; use bounded `wre_search` when it is unavailable.
