# WRE onboarding helpers

These helpers supplement the v6.1.2 runtime package. Follow
[DEPLOYMENT.md](../DEPLOYMENT.md) for the complete sequence and download them
from the [v6.1.2 Release assets](https://github.com/A2Sumie/windows-remote-executor/releases/tag/v6.1.2).
Check `ONBOARDING_SHA256SUMS.txt` before execution.

| Helper | Runs on | Behavior |
|---|---|---|
| `create-wre-target.py` | macOS/Linux controller | Discover an exact online Tailscale peer; save the private env; export public key and Token SHA256 |
| `initialize-pwsh7.ps1` | pwsh 7 on Windows/macOS/Linux | Validate Core runtime, optionally install PSScriptAnalyzer, select the existing Windows Terminal Core profile |
| `initialize-wre.ps1` | Windows Administrator pwsh 7 console | Validate assigned address/account/package, authorize the key, ensure NetSecurity firewall, install WRE and inspect the listener/tasks |

The private target directory defaults to `~/.config/wre/targets` (0700),
with env/bootstrap files at 0600. Existing credentials require explicit
`--reuse` or `--rotate-token`; rotation retains a private backup. The plain
Token and SSH private key stay on the controller.

Both PowerShell helpers support `-CheckOnly`. The WRE check runs the verified
package's selftest and checks prerequisites without changing host
configuration. Actual installation requires a local Administrator console,
retains policy/key/apply-spec backups and writes a phase/error JSON report.
Existing business task/apply-spec fields are preserved during reinstallation.

Use `initialize-pwsh7.ps1 -InstallAnalyzer` for the local parsing/lint runtime.
The analyzer is optional on Windows hosts that only execute scripts. A
current-user WindowsApps executable discovery does not establish SYSTEM
availability; use a machine-wide MSI installation for that lane.

## Verification

Controller CLI tests cover secret separation, private file permissions,
quoted paths, credential preservation/rotation, input rejection and exact
online peer selection:

```bash
python3 -m unittest discover -s onboarding/tests -v
```

During the 2026-10-14 update, the controller tests and native PowerShell
syntax/PSScriptAnalyzer gates passed. The helpers were checked on macOS
Core 7.6.0 and Windows Core 7.6.6, including the real release package selftest
in Windows read-only preflight. Isolated Terminal fixtures exercised the
settings update and byte-identical backup.

This verification did not perform a fresh privileged installation on a new
Windows machine. Existing fleet runtime/credentials were retained during
the onboarding checks. After an actual install, verify token-required WRE
RPC, process execution, exact active listener and required SYSTEM tasks as
shown in the deployment guide.
