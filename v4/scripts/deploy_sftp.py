"""WRE v4 light deploy — SFTP-only, no elevation.

For Windows hosts where sshd_config has no ForceCommand (typical case for the
Codex / Tailscale private-only setup), the v4 controller invokes the v4
native entry directly via `cmd /d /s /c python.exe rpc.py rpc-stdio`. This
works as long as the v4 tree exists at `C:/CodexRemote/wre/` AND
`access-policy.json` is in place.

This script:
  1. Reads the existing v3 access-policy.json on the target (to preserve the
     current `accessTokenSha256`) so v4 enforcement accepts the same TOKEN.
  2. Updates commandMode to "v4".
  3. SFTP-uploads the build's wre/ tree to C:/CodexRemote/wre/.
  4. SFTP-uploads the modified access-policy.json.

When this script returns, the controller can immediately call v4 RPC. The
v3 native remains installed at C:/CodexRemote/tools/ and v3's scheduled heal
tasks continue using v3 native. Use `deploy-wre.py --tasks-only` later to
swap them, with elevation (separate step).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
sys.path.insert(0, str(HERE.parent.parent / "windows-remote-executor" / "lib"))

from v4.controller import sftp, targets  # noqa: E402
from v4.controller import client as v3rpc  # for loading only; actually uses v3 for read  # noqa: E402

V3_TOOLS_POLICY = "C:/CodexRemote/tools/access-policy.json"
V4_DEST = "C:/CodexRemote/wre"
V4_DEST_POLICY = f"{V4_DEST}/access-policy.json"


def _load_v3_policy_via_v3_client(target) -> dict:
    """Read the existing v3 access-policy.json using v3 native."""
    sys.path.insert(0, str(HERE.parent.parent / "windows-remote-executor" / "lib"))
    import win_remote_cli as cli  # type: ignore
    import wre_v3_client as v3  # type: ignore
    # cli.Target vs v4 Target differ; reuse the v3 pipeline directly.
    v3_target = cli.load_target(target.name)
    call = v3.call_rpc(v3_target, "file.readText", {"path": V3_TOOLS_POLICY, "maxBytes": 8000})
    if not call.ok:
        raise SystemExit(f"cannot read v3 access-policy.json: {call.stderr_text or call.response.get('errorClass')}")
    return json.loads(call.response.get("stdoutText") or "{}")


def _load_existing_v4_policy_via_sftp(target) -> dict:
    """Fallback for v4-only hosts (no v3 native): preserve the live v4 policy."""
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="wre-policy-")) / "access-policy.json"
    try:
        sftp.get(target, V4_DEST_POLICY, tmp)
        return json.loads(tmp.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _build_v4_policy(v3_policy: dict) -> dict:
    return {
        "expectedListenAddress": v3_policy.get("expectedListenAddress") or "",
        "exposureMode": v3_policy.get("exposureMode") or "private-only",
        "commandMode": "v4",
        "label": v3_policy.get("label") or "PRIVATE-ONLY",
        "accessTokenSha256": v3_policy.get("accessTokenSha256"),
        "updatedAt": _now_iso(),
    }


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _sftp_walk_upload(target, local_root: Path, remote_root: str) -> tuple[int, Path]:
    """Stage files locally as a tarball, scp-upload, then ssh-extract on Windows.

    Windows OpenSSH sftp-server has path quirks; tar+scp+ssh-tar avoids them
    and reduces per-file round-trips for ~1000 small files.
    """
    import subprocess
    import tempfile

    print(f"[deploy-sftp] staging local tar of {local_root}")
    tmpdir = Path(tempfile.mkdtemp(prefix="wre-deploy-"))
    tar_path = tmpdir / "wre-v4-deploy.tar.gz"
    # Build tar.gz with top-level "wre/" so extraction at C:/CodexRemote/ yields
    # C:/CodexRemote/wre/...
    import tarfile
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(local_root, arcname="wre")
    print(f"[deploy-sftp] tarball size: {tar_path.stat().st_size} bytes")

    remote_tar = "C:/CodexRemote/inbox/wre-v4-deploy.tar.gz"

    # scp upload
    scp_args = [
        "scp", "-P", str(target.port), "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
    ]
    if target.key:
        scp_args += ["-i", target.key]
    scp_args += [str(tar_path), f"{target.ssh_destination}:{remote_tar}"]
    print(f"[deploy-sftp] scp upload -> {remote_tar}")
    completed = subprocess.run(scp_args, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, check=False)
    if completed.returncode != 0:
        raise SystemExit(
            f"scp upload failed (rc={completed.returncode}):\n{completed.stderr}{completed.stdout}"
        )

    # ssh-tar extract on Windows (Windows System32 tar understands .tar.gz).
    n = _count_files(local_root)
    remote_tar_win = remote_tar.replace("/", "\\")
    # Three separate ssh commands avoid the fragile nested `&&` chain inside
    # a single `cmd /c "..."` while still being idempotent: inbox already
    # exists, wre/ may already exist, the tarball is overwritten.
    ssh_base = [
        "ssh", "-p", str(target.port), "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
    ]
    if target.key:
        ssh_base += ["-i", target.key]
    ssh_base += [target.ssh_destination]

    print(f"[deploy-sftp] ssh tar -xf on Windows -> {V4_DEST}")
    for cmd in (
        f"cmd.exe /d /s /c \"if not exist C:\\CodexRemote\\inbox mkdir C:\\CodexRemote\\inbox\"",
        f"cmd.exe /d /s /c \"tar -xf {remote_tar_win} -C C:\\CodexRemote\\\"",
        f"cmd.exe /d /s /c \"del {remote_tar_win}\"",
    ):
        completed = subprocess.run(
            [*ssh_base, cmd],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
        )
        if completed.returncode != 0:
            raise SystemExit(
                f"ssh step failed (rc={completed.returncode}, cmd={cmd!r}):\n"
                f"stderr: {completed.stderr}\nstdout: {completed.stdout}"
            )

    return n, tar_path


def _count_files(local_root: Path) -> int:
    n = 0
    for entry in local_root.rglob("*"):
        if entry.is_file():
            n += 1
    return n


def shell(s: str) -> str:
    import shlex
    return shlex.quote(s)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target")
    parser.add_argument("--build-dir", default=str(HERE.parent / "build" / "wre-0.4.0-windows-x64" / "wre"),
                        help="local build dir for v4 (default: matching make_bootstrap_package.py output)")
    parser.add_argument("--mode", choices=["full", "tasks-only", "files-only"], default="full")
    args = parser.parse_args(argv)

    target = targets.load_target(args.target)
    print(f"[deploy-sftp] target={target.name} ({target.ssh_destination})")

    print("[deploy-sftp] read existing v3 access-policy.json")
    try:
        v3_policy = _load_v3_policy_via_v3_client(target)
    except Exception as exc:
        print(f"[deploy-sftp] v3 policy read failed ({type(exc).__name__}); "
              "falling back to live v4 access-policy.json")
        v3_policy = _load_existing_v4_policy_via_sftp(target)
    v4_policy = _build_v4_policy(v3_policy)
    sha = v4_policy.get("accessTokenSha256") or ""
    print(f"[deploy-sftp] v4 policy: commandMode=v4 sha256={sha[:12]}...")

    if args.mode in ("full", "files-only"):
        local_build = Path(args.build_dir)
        if not local_build.is_dir():
            raise SystemExit(f"missing build dir: {local_build}; run make_bootstrap_package.py first")
        # Stash the local-access-policy.json (no, write a single file).
        # Upload the entire wre tree (~18MB python + rpc.py + actions + win32)
        print(f"[deploy-sftp] uploading {local_build} -> {V4_DEST}/")
        n = _sftp_walk_upload(target, local_build, V4_DEST)
        print(f"[deploy-sftp] uploaded {n} files")

        # Write the v4 access-policy.json locally and SFTP it.
        # Write the v4 access-policy.json inside the local wre tree so tar
        # packages it together with the source. Avoids an extra SFTP step.
        local_policy_path = local_build / "access-policy.json"
        local_policy_path.write_text(
            json.dumps(v4_policy, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"[deploy-sftp] staged local policy: {local_policy_path}")

        n, tar_path = _sftp_walk_upload(target, local_build, V4_DEST)
        print(f"[deploy-sftp] uploaded {n} files; access-policy at {V4_DEST_POLICY}")

    print()
    print("== deploy-sftp complete ==")
    print(f"Next step: python3 -m v4.scripts.verify_v4_remote {target.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())