"""WRE v6 light deploy — SFTP-only, no elevation. Sidecar-capable.

Uploads the v6 build tree (embeddable Python + native source) and an
access-policy.json to the target in ONE staged tarball.

Layout flags (post-2026-08-19 rebrand: WRE brand is the default):
  --root       on-host root dir (default C:/WRE). The tarball
               extracts here; inbox is <root>/inbox.
  --entry-root entry tree dir (default C:/WRE/wre). For a sidecar
               deploy pass --entry-root C:/WRE/wre6 — the default
               C:/WRE/wre tree is not touched. Must be under --root.
               (Legacy fleet root is C:/CodexRemote; pass
               --root C:/CodexRemote --entry-root C:/CodexRemote/wre6
               to sidecar next to the legacy tree.)

Token resolution order (first hit wins; NO source => ABORT, v6 never
assembles a null-token policy):
  a. --access-token <plain> (hashed locally)
  b. TARGET_ACCESS_TOKEN in the target .env file (hashed locally)
  c. existing v3 access-policy.json (read via the v3 bridge)
  d. live policy at <entry-root>/access-policy.json (SFTP GET)

When this script returns, point the controller at the entry root:
    WRE_ENTRY=C:/WRE/wre6 python3 -m v6.controller.shell X570 --info
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

from v6.controller import sftp, targets  # noqa: E402

# LEGACY v3 path on the live fleet — do not rebrand; the v3 tree still
# physically lives at C:/CodexRemote/tools/ on X570.
V3_TOOLS_POLICY = "C:/CodexRemote/tools/access-policy.json"


def _load_v3_policy_via_v3_client(target) -> dict:
    """Read the existing v3 access-policy.json using v3 native."""
    import win_remote_cli as cli  # type: ignore
    import wre_v3_client as v3  # type: ignore
    v3_target = cli.load_target(target.name)
    call = v3.call_rpc(v3_target, "file.readText", {"path": V3_TOOLS_POLICY, "maxBytes": 8000})
    if not call.ok:
        raise RuntimeError(f"cannot read v3 access-policy.json: {call.stderr_text or call.response.get('errorClass')}")
    return json.loads(call.response.get("stdoutText") or "{}")


def _load_live_policy_via_sftp(target, policy_path: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="wre-policy-") as tmpdir:
        tmp = Path(tmpdir) / "access-policy.json"
        sftp.get(target, policy_path, tmp)
        return json.loads(tmp.read_text(encoding="utf-8-sig"))


def _resolve_token_sha(target, access_token: str | None, policy_path: str) -> tuple[str, str]:
    """Return (token_sha256, source). Aborts when no token can be determined —
    a null-token policy is never assembled (v5/v6 fail-closed contract)."""
    if access_token:
        return hashlib.sha256(access_token.encode("utf-8")).hexdigest(), "--access-token"
    if target.access_token:
        return hashlib.sha256(target.access_token.encode("utf-8")).hexdigest(), \
            "TARGET_ACCESS_TOKEN from target env file"

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
        live_policy = _load_live_policy_via_sftp(target, policy_path)
        sha = live_policy.get("accessTokenSha256")
        if sha:
            return str(sha), f"live {policy_path}"
        errors.append("live policy readable but accessTokenSha256 is null")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"live policy read failed: {type(exc).__name__}: {exc}")

    detail = "; ".join(errors) if errors else "no policy source available"
    raise SystemExit(
        "[deploy-sftp] ERROR: could not determine an access token hash from any "
        f"source ({detail}). Refusing to deploy a null-token policy. Re-run "
        "with --access-token <plain-token> to set one explicitly."
    )


def _build_policy(token_sha: str, expected_listen: str | None) -> dict:
    return {
        "expectedListenAddress": expected_listen or "",
        "exposureMode": "private-only",
        "commandMode": "v6",
        "label": "PRIVATE-ONLY",
        "accessTokenSha256": token_sha,
        "updatedAt": _now_iso(),
    }


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _count_files(local_root: Path) -> int:
    return sum(1 for entry in local_root.rglob("*") if entry.is_file())


def _stage_and_upload(target, build_dir: Path, policy: dict, *,
                      root: str, entry_root: str) -> int:
    """Stage build tree + policy, then tar+scp+ssh-extract ONCE at <root>."""
    root = root.rstrip("/\\")
    entry_root = entry_root.rstrip("/\\")
    entry_name = entry_root.split("/")[-1]
    if not entry_root.lower().startswith(root.lower() + "/"):
        raise SystemExit(f"[deploy-sftp] --entry-root ({entry_root}) must live under --root ({root})")

    with tempfile.TemporaryDirectory(prefix="wre-deploy-") as tmpdir:
        staging = Path(tmpdir) / entry_name
        print(f"[deploy-sftp] staging {build_dir} -> {staging}")
        shutil.copytree(build_dir, staging)
        (staging / "access-policy.json").write_text(
            json.dumps(policy, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        n = _count_files(staging)

        tar_path = Path(tmpdir) / "wre-v6-deploy.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(staging, arcname=entry_name)
        print(f"[deploy-sftp] tarball size: {tar_path.stat().st_size} bytes ({n} files)")

        remote_tar = f"{root}/inbox/wre-v6-deploy.tar.gz"

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

        ssh_base = [
            "ssh", "-p", str(target.port), "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
        ]
        if target.key:
            ssh_base += ["-i", target.key]
        ssh_base += [target.ssh_destination]

        root_win = root.replace("/", "\\")
        remote_tar_win = remote_tar.replace("/", "\\")
        print(f"[deploy-sftp] ssh tar -xf on Windows -> {entry_root}")
        for cmd in (
            f"cmd.exe /d /s /c \"if not exist {root_win}\\inbox mkdir {root_win}\\inbox\"",
            f"cmd.exe /d /s /c \"tar -xf {remote_tar_win} -C {root_win}\\\"",
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
    parser.add_argument("--build-dir",
                        default=str(HERE.parent / "build" / "wre-6.0.0-windows-x64" / "wre"),
                        help="local build dir (default: make_bootstrap_package.py output)")
    parser.add_argument("--root", default="C:/WRE",
                        help="on-host root dir (default C:/WRE)")
    parser.add_argument("--entry-root", default="C:/WRE/wre",
                        help="entry tree dir (default C:/WRE/wre; "
                             "sidecar: C:/WRE/wre6)")
    parser.add_argument("--access-token", default=None,
                        help="Plain access token to install (hashed). Falls back to "
                             "TARGET_ACCESS_TOKEN in the target env, then v3/live policy.")
    args = parser.parse_args(argv)

    target = targets.load_target(args.target)
    entry_root = args.entry_root.rstrip("/\\")
    print(f"[deploy-sftp] target={target.name} ({target.ssh_destination}) "
          f"entry={entry_root}")

    policy_path = f"{entry_root}/access-policy.json"
    token_sha, source = _resolve_token_sha(target, args.access_token, policy_path)
    v6_policy = _build_policy(token_sha, target.expected_listen_address)
    print(f"[deploy-sftp] token sha256={token_sha[:12]}... (source: {source}); commandMode=v6")

    local_build = Path(args.build_dir)
    if not local_build.is_dir():
        raise SystemExit(f"missing build dir: {local_build}; run make_bootstrap_package.py first")
    print(f"[deploy-sftp] uploading {local_build} -> {entry_root}/ (single staged tar)")
    n = _stage_and_upload(target, local_build, v6_policy,
                          root=args.root, entry_root=entry_root)
    print(f"[deploy-sftp] uploaded {n} files; access-policy at {policy_path}")

    print()
    print("== deploy-sftp complete ==")
    print(f"Next: WRE_ENTRY={entry_root} PYTHONPATH=. "
          f"python3 -m v6.scripts.verify_v6_remote {target.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
