"""WRE v5 light deploy — SFTP-only, no elevation.

For Windows hosts where sshd_config has no ForceCommand (typical case for the
Codex / Tailscale private-only setup), the v5 controller invokes the v5
native entry directly via `cmd /d /s /c python.exe rpc.py rpc-stdio`. This
works as long as the v5 tree exists at `C:/CodexRemote/wre/` AND
`access-policy.json` is in place.

This script:
  1. Determines the policy token hash, in precedence order:
       a. --access-token (explicit operator-provided plain token), or
       b. the existing v3 access-policy.json on the target (read via the v3
          native bridge), or
       c. the live v4/v5 access-policy.json at C:/CodexRemote/wre/ (SFTP GET).
     If NONE of these yields a token hash, the deploy ABORTS with an explicit
     error — v5 never assembles a null-token policy (fail-closed; v4 silently
     deployed `{}`, which then fail-opened everything).
  2. Stages the build tree + access-policy.json into a local temp directory
     and uploads ONE tarball (v4 uploaded the ~18 MB tree twice by mistake).
  3. ssh-extracts the tarball at C:/CodexRemote/ (overwrites C:/CodexRemote/wre/).

When this script returns, the controller can immediately call v5 RPC. The
v3 native remains installed at C:/CodexRemote/tools/ and v3's scheduled heal
tasks continue using v3 native. Use `deploy-wre.py --tasks-only` later to
swap them, with elevation (separate step).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
sys.path.insert(0, str(HERE.parent.parent / "windows-remote-executor" / "lib"))

from v5.controller import sftp, targets  # noqa: E402

V3_TOOLS_POLICY = "C:/CodexRemote/tools/access-policy.json"
V5_DEST = "C:/CodexRemote/wre"
V5_DEST_POLICY = f"{V5_DEST}/access-policy.json"


def _load_v3_policy_via_v3_client(target) -> dict:
    """Read the existing v3 access-policy.json using v3 native."""
    sys.path.insert(0, str(HERE.parent.parent / "windows-remote-executor" / "lib"))
    import win_remote_cli as cli  # type: ignore
    import wre_v3_client as v3  # type: ignore
    # cli.Target vs v5 Target differ; reuse the v3 pipeline directly.
    v3_target = cli.load_target(target.name)
    call = v3.call_rpc(v3_target, "file.readText", {"path": V3_TOOLS_POLICY, "maxBytes": 8000})
    if not call.ok:
        raise RuntimeError(f"cannot read v3 access-policy.json: {call.stderr_text or call.response.get('errorClass')}")
    return json.loads(call.response.get("stdoutText") or "{}")


def _load_existing_v5_policy_via_sftp(target) -> dict:
    """Fallback for v4/v5-only hosts (no v3 native): preserve the live policy."""
    with tempfile.TemporaryDirectory(prefix="wre-policy-") as tmpdir:
        tmp = Path(tmpdir) / "access-policy.json"
        sftp.get(target, V5_DEST_POLICY, tmp)
        return json.loads(tmp.read_text(encoding="utf-8-sig"))


def _resolve_token_sha(target, access_token: str | None) -> tuple[str, str]:
    """Return (token_sha256, source_description). Aborts the deploy when no
    token can be determined — a null-token policy is never assembled."""
    if access_token:
        return hashlib.sha256(access_token.encode("utf-8")).hexdigest(), "--access-token"

    errors: list[str] = []
    try:
        v3_policy = _load_v3_policy_via_v3_client(target)
        sha = v3_policy.get("accessTokenSha256")
        if sha:
            return str(sha), "v3 access-policy.json"
        errors.append("v3 policy readable but accessTokenSha256 is null")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"v3 policy read failed: {type(exc).__name__}: {exc}")

    try:
        live_policy = _load_existing_v5_policy_via_sftp(target)
        sha = live_policy.get("accessTokenSha256")
        if sha:
            return str(sha), "live C:/CodexRemote/wre/access-policy.json"
        errors.append("live policy readable but accessTokenSha256 is null")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"live policy read failed: {type(exc).__name__}: {exc}")

    detail = "; ".join(errors) if errors else "no policy source available"
    raise SystemExit(
        "[deploy-sftp] ERROR: could not determine an access token hash from any "
        f"source ({detail}). Refusing to deploy a null-token policy (v5 is "
        "fail-closed: it would deny every action, and v4 fail-open was the "
        "audit finding). Re-run with --access-token <plain-token> to set one "
        "explicitly."
    )


def _build_v5_policy(token_sha: str, expected_listen: str | None) -> dict:
    return {
        "expectedListenAddress": expected_listen or "",
        "exposureMode": "private-only",
        "commandMode": "v5",
        "label": "PRIVATE-ONLY",
        "accessTokenSha256": token_sha,
        "updatedAt": _now_iso(),
    }


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _count_files(local_root: Path) -> int:
    n = 0
    for entry in local_root.rglob("*"):
        if entry.is_file():
            n += 1
    return n


def _stage_and_upload(target, build_dir: Path, policy: dict) -> int:
    """Copy the build tree into a TemporaryDirectory, drop access-policy.json
    inside it, then tar+scp+ssh-extract ONCE.

    v4 wrote the policy into the shared build dir and ran the whole upload
    twice (~18 MB x2). Staging keeps the build dir pristine and
    TemporaryDirectory guarantees cleanup.
    """
    with tempfile.TemporaryDirectory(prefix="wre-deploy-") as tmpdir:
        staging = Path(tmpdir) / "wre"
        print(f"[deploy-sftp] staging {build_dir} -> {staging}")
        shutil.copytree(build_dir, staging)
        (staging / "access-policy.json").write_text(
            json.dumps(policy, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        n = _count_files(staging)

        tar_path = Path(tmpdir) / "wre-v5-deploy.tar.gz"
        # Top-level "wre/" in the tar: extraction at C:/CodexRemote/ yields
        # C:/CodexRemote/wre/...
        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(staging, arcname="wre")
        print(f"[deploy-sftp] tarball size: {tar_path.stat().st_size} bytes ({n} files)")

        remote_tar = "C:/CodexRemote/inbox/wre-v5-deploy.tar.gz"

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

        remote_tar_win = remote_tar.replace("/", "\\")
        print(f"[deploy-sftp] ssh tar -xf on Windows -> {V5_DEST}")
        for cmd in (
            "cmd.exe /d /s /c \"if not exist C:\\CodexRemote\\inbox mkdir C:\\CodexRemote\\inbox\"",
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
    return n


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target")
    parser.add_argument("--build-dir", default=str(HERE.parent / "build" / "wre-5.0.0-windows-x64" / "wre"),
                        help="local build dir for v5 (default: matching make_bootstrap_package.py output)")
    parser.add_argument("--access-token", default=None,
                        help="Plain access token to install (hashed). Required when no existing "
                             "v3/v4/v5 policy with a token can be read from the target.")
    args = parser.parse_args(argv)

    target = targets.load_target(args.target)
    print(f"[deploy-sftp] target={target.name} ({target.ssh_destination})")

    token_sha, source = _resolve_token_sha(target, args.access_token)
    v5_policy = _build_v5_policy(token_sha, target.expected_listen_address)
    print(f"[deploy-sftp] token sha256={token_sha[:12]}... (source: {source}); commandMode=v5")

    local_build = Path(args.build_dir)
    if not local_build.is_dir():
        raise SystemExit(f"missing build dir: {local_build}; run make_bootstrap_package.py first")
    print(f"[deploy-sftp] uploading {local_build} -> {V5_DEST}/ (single staged tar)")
    n = _stage_and_upload(target, local_build, v5_policy)
    print(f"[deploy-sftp] uploaded {n} files; access-policy at {V5_DEST_POLICY}")

    print()
    print("== deploy-sftp complete ==")
    print(f"Next step: PYTHONPATH=. python3 -m v5.scripts.verify_v5_remote {target.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
