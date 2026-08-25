"""On-host WRE v6 installer — run with elevation on the Windows target.

Layout inside the bootstrap package:
    wre-v6.0.0-windows-x64/
        python/                     <- embeddable python 3.12
        wre/                        <- copied to C:/WRE/wre/
            rpc.py
            actions/...
            win32/...
        deploy-wre.py               <- this script

Operator (on the Windows host) elevates to Administrator and runs:
    python\\pythonw.exe deploy-wre.py --target-name X570 --expected-listen 100.119.106.8 --access-token <plain>

After deploy, remote control flows through:
    Client -> ssh X570 -> cmd /c "pythonw -I -X utf8 C:/WRE/wre/rpc.py rpc-stdio"
                                   + JSON on stdin -> JSON on stdout

The on-host execution uses the same embedded python (via the install-time
python_path) — no host Python is required.

v6 deploy note: same fail-closed/token-preserving behaviour as v5.
v5 deploy changes (2026-08-18 v4 audit):
  - Exactly one of --access-token / --keep-existing-policy is REQUIRED for a
    full deploy. There is no null-token default policy (v5 native denies
    everything when accessTokenSha256 is null — fail-closed).
  - ForceCommand is OPT-IN (--force-command). Writing it kills the SSH user's
    sftp/scp/interactive sessions (rpc.py does not dispatch
    SSH_ORIGINAL_COMMAND) and breaks the deploy_sftp loop — so v5 leaves
    sshd_config's ForceCommand alone unless explicitly asked.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DEST = Path("C:/WRE/wre")


def _run_as_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _info(msg: str) -> None:
    print(f"[deploy-wre] {msg}", flush=True)


def _die(msg: str, code: int = 2) -> None:
    print(f"[deploy-wre] ERROR: {msg}", file=sys.stderr, flush=True)
    raise SystemExit(code)


def selftest(entry_path: Path, python_exe: Path) -> bool:
    """Run v6 native `selftest` via local python invocation."""
    _info(f"selftest invocation: {python_exe} {entry_path} selftest")
    import subprocess
    completed = subprocess.run(
        [str(python_exe), "-I", "-X", "utf8", str(entry_path), "selftest"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        _info(f"selftest returncode={completed.returncode}\n  stderr: {completed.stderr.strip()[:400]}")
        return False
    try:
        response = json.loads(completed.stdout.strip().splitlines()[-1])
    except Exception as exc:
        _info(f"selftest parse error: {exc}\n  raw: {completed.stdout[:400]}")
        return False
    actions = response.get("data", {}).get("actions") or []
    if not actions:
        _info(f"selftest did not return any actions. raw: {completed.stdout[:400]}")
        return False
    _info(f"selftest ok: protocolVersion={response.get('protocolVersion')} actions={len(actions)}")
    return True


def deploy_source(package_wre_dir: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if not package_wre_dir.is_dir():
        _die(f"missing package wre dir: {package_wre_dir}")
    for entry in package_wre_dir.rglob("*"):
        rel = entry.relative_to(package_wre_dir)
        target = dest / rel
        if entry.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(entry, target)
    _info(f"v6 tree copied to {dest}")


def install_access_policy(dest: Path, listen: str, token: str) -> Path:
    """Write access-policy.json with the sha256 of the operator-provided token.

    v6 never writes a null-token policy: a null token fail-closes the bridge
    (every action denied except host.capabilities), which is a lockout.
    """
    if not token:
        _die("install_access_policy called without a token; refusing to write a null-token policy")
    import hashlib
    policy = {
        "exposureMode": "private-only",
        "commandMode": "v6",
        "label": "PRIVATE-ONLY",
        "expectedListenAddress": listen,
        "accessTokenSha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    policy_path = dest / "access-policy.json"
    tmp = dest / f"access-policy.json.wre-tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(policy, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, policy_path)
    _info(f"access-policy written: {policy_path}")
    return policy_path


def backup_and_rewrite_sshd_config(dest: Path, expected_listen: str, force_command: bool) -> dict:
    """Backup `C:/ProgramData/ssh/sshd_config` and rewrite ListenAddress
    (+ ForceCommand only when --force-command was given)."""
    sys.path.insert(0, str(dest))
    from win32 import sshd as sshd_mod  # noqa: E402
    backup_dir = dest / "backup"
    backup_dir.mkdir(exist_ok=True)
    ts = _now()
    config_path = Path("C:/ProgramData/ssh/sshd_config")
    if not config_path.is_file():
        _info(f"sshd_config not found at {config_path}; skipping rewrite")
        return {"rewritten": False, "reason": "sshd_config missing"}
    backup_path = backup_dir / f"sshd_config.{ts}.bak"
    shutil.copy2(config_path, backup_path)
    _info(f"sshd_config backup -> {backup_path}")

    original = sshd_mod.read_full_config()
    new_text = _patch_sshd_config(original, expected_listen, force_command=force_command)
    if new_text == original:
        _info("sshd_config already pinned; no change")
        return {"rewritten": False, "backup": str(backup_path), "reason": "already pinned"}
    sshd_mod._atomic_write(str(config_path), new_text)
    _info("sshd_config updated (ListenAddress"
          + (" + ForceCommand -> WRE v6)" if force_command else ")"))
    return {"rewritten": True, "backup": str(backup_path)}


WRE_FORCE_COMMAND = "pythonw.exe -I -X utf8 C:/WRE/wre/rpc.py rpc-stdio"
LEGACY_NATIVE_FRAGMENTS = (
    "WindowsRemoteExecutor.Native.exe",
    "WindowsRemoteExecutor.cmd",
    "CodexRemote/tools/",  # LEGACY v3 install marker in old sshd ForceCommand lines — keep forever
)
PRE_WRE_FORCE_COMMAND_FRAGMENTS = (
    "WRE-v4",  # set by v4 deploys — safe to overwrite
) + LEGACY_NATIVE_FRAGMENTS


def _patch_sshd_config(text: str, expected_listen: str, *, force_command: bool) -> str:
    lines = text.splitlines()
    out: list[str] = []
    saw_listen = False
    saw_force = False
    in_match = False
    for raw in lines:
        line = raw.strip()
        if line and not line.startswith("#"):
            tokens = line.split()
            key = tokens[0].lower()
            if key == "match":
                in_match = True
            if key in ("listenaddress", "listen") and not in_match:
                if saw_listen:
                    continue
                out.append(f"ListenAddress {expected_listen}")
                saw_listen = True
                continue
            if force_command and key == "forcecommand" and not in_match:
                # Replace a stale WRE/v3-native ForceCommand; keep any other
                # operator-configured ForceCommand untouched (only the FIRST
                # line is effective in sshd, but we do not silently delete
                # configuration we do not own).
                if saw_force:
                    continue
                if any(frag in line for frag in PRE_WRE_FORCE_COMMAND_FRAGMENTS):
                    out.append(f"ForceCommand {WRE_FORCE_COMMAND}")
                    saw_force = True
                    continue
                saw_force = True
                out.append(raw)
                continue
        out.append(raw)
    missing: list[str] = []
    if not saw_listen and expected_listen:
        missing.append(f"ListenAddress {expected_listen}")
    if force_command and not saw_force:
        missing.append(f"ForceCommand {WRE_FORCE_COMMAND}")
    if missing:
        insert_at = None
        for idx, raw in enumerate(out):
            line = raw.strip()
            if line and not line.startswith("#") and line.lower().split()[0] == "match":
                insert_at = idx
                break
        if insert_at is None:
            out.extend(missing)
        else:
            block = list(missing)
            if insert_at > 0 and out[insert_at - 1].strip():
                block = [""] + block
            block = block + [""]
            out[insert_at:insert_at] = block
    # Preserve the original trailing-newline convention.
    return "\n".join(out) + ("\n" if text.endswith(("\n", "\r")) else "")


def restart_sshd_service(dest: Path) -> None:
    sys.path.insert(0, str(dest))
    from win32 import service as svc_mod  # noqa: E402
    result = svc_mod.restart_service_safe("sshd", timeout_seconds=20)
    _info(f"sshd restart: {result}")


def ensure_scheduled_tasks(dest: Path, expected_listen: str) -> None:
    sys.path.insert(0, str(dest))
    from win32 import scheduled_tasks as tasks_mod  # noqa: E402
    result = tasks_mod.ensure_repair_tasks(expected_listen=expected_listen)
    _info(f"repair tasks: created={result['created']} errors={result['errors']}")


def ensure_streamserv_task(dest: Path, root: str, run_as_user: str) -> None:
    sys.path.insert(0, str(dest))
    from win32 import scheduled_tasks as tasks_mod  # noqa: E402
    result = tasks_mod.ensure_streamserv_task(root=root, run_as_user=run_as_user)
    _info(f"streamserv task: {result}")


def ensure_apply_agent(dest: Path, expected_listen: str) -> None:
    sys.path.insert(0, str(dest))
    from win32 import scheduled_tasks as tasks_mod  # noqa: E402
    result = tasks_mod.ensure_apply_agent_task(expected_listen=expected_listen)
    _info(f"apply agent task: {result}")


def write_apply_spec(dest: Path, expected_listen: str, register_streamserv: bool,
                     streamserv_root: str, streamserv_run_as: str) -> None:
    """Persist what the SYSTEM apply-agent should ensure on each run."""
    resolved_run_as = streamserv_run_as
    if register_streamserv:
        sys.path.insert(0, str(dest))
        from win32 import scheduled_tasks as tasks_mod  # noqa: E402
        resolved_run_as = tasks_mod.resolve_run_as_user(streamserv_run_as)
    spec = {
        "expectedListenAddress": expected_listen,
        "ensureRepairTasks": True,
        "streamserv": ({"root": streamserv_root, "runAsUser": resolved_run_as}
                       if register_streamserv else False),
        "tasks": [],
    }
    path = dest / "apply-tasks.json"
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(spec, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    _info(f"apply spec written: {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-wre-dir", default=None,
                        help="Path that contains the v6 source tree. Default: this directory if rpc.py exists, else ./wre inside a bootstrap package")
    parser.add_argument("--dest", default=str(DEFAULT_DEST),
                        help="Destination directory on the host (default C:/WRE/wre)")
    parser.add_argument("--python-exe", default=None,
                        help="Override the pythonw.exe used for on-host selftest and repair tasks")
    parser.add_argument("--target-name", required=True)
    parser.add_argument("--expected-listen", required=True)
    parser.add_argument("--access-token", default=None,
                        help="Plain access token to install; hashed into access-policy.json")
    parser.add_argument("--keep-existing-policy", action="store_true",
                        help="Do not touch access-policy.json (preserve the live token). "
                             "Exactly one of --access-token / --keep-existing-policy is required.")
    parser.add_argument("--force-command", action="store_true",
                        help="Opt-in: write `ForceCommand pythonw.exe ... rpc.py rpc-stdio` into "
                             "sshd_config. WARNING: this KILLS sftp/scp/interactive ssh for the "
                             "user (rpc.py does not dispatch SSH_ORIGINAL_COMMAND) and breaks "
                             "deploy_sftp. The default controller command already pins the entry; "
                             "leave this off unless you know why you want it.")
    parser.add_argument("--no-restart-sshd", action="store_true",
                        help="Skip sshd restart (operator will restart manually)")
    parser.add_argument("--skip-tasks", action="store_true",
                        help="Skip scheduled-task re-creation")
    parser.add_argument("--tasks-only", action="store_true",
                        help="Only selftest installed source and register scheduled tasks; do not copy files, write policy, rewrite sshd_config, or restart sshd")
    parser.add_argument("--register-streamserv", action="store_true",
                        help="Also register 'WRE StreamServ Start' (TaskScheduler COM) for X570")
    parser.add_argument("--streamserv-root", default="D:/StreamServ",
                        help="StreamServ root used with --register-streamserv")
    parser.add_argument("--streamserv-run-as", default="SYSTEM",
                        help="Task principal for StreamServ launcher; use CURRENT to register as the elevated PowerShell user")
    args = parser.parse_args(argv)

    _info(f"target={args.target_name} expected_listen={args.expected_listen}")
    if not args.tasks_only:
        if bool(args.access_token) == bool(args.keep_existing_policy):
            _die("exactly one of --access-token / --keep-existing-policy is required "
                 "(v6 has no null-token default policy)")
    if os.name != "nt":
        _die("deploy-wre.py must be run on Windows.")
    if not _run_as_admin():
        _die("must be run elevated (Administrator). Right-click -> Run as administrator.")

    if args.force_command:
        _info("!! --force-command requested: sshd_config will get")
        _info(f"!!   ForceCommand {WRE_FORCE_COMMAND}")
        _info("!! This KILLS sftp/scp/interactive ssh for this user (rpc.py does")
        _info("!! not dispatch SSH_ORIGINAL_COMMAND) and breaks deploy_sftp loops.")

    package_wre_dir = Path(args.package_wre_dir) if args.package_wre_dir else (
        HERE if (HERE / "rpc.py").is_file() else HERE / "wre"
    )
    python_dir = package_wre_dir / "python"
    python_exe = Path(args.python_exe) if args.python_exe else (
        python_dir / "pythonw.exe" if (python_dir / "pythonw.exe").exists() else
        python_dir / "python.exe"
    )
    if not python_exe.is_file():
        _die(f"package python not found: {python_exe}")
    _info(f"using python: {python_exe}")

    wre_entry = package_wre_dir / "rpc.py"
    if not wre_entry.is_file():
        _die(f"missing v6 entry: {wre_entry}")

    dest = Path(args.dest)

    if args.tasks_only:
        # Files are already deployed via deploy_sftp; only register tasks.
        dest_entry = dest / "rpc.py"
        if not dest_entry.is_file():
            _die(f"--tasks-only requires an already-deployed tree at {dest}")
        _info(f"step: selftest (installed source {dest_entry})")
        if not selftest(dest_entry, python_exe):
            _die("selftest failed; installed tree looks broken")
        if not args.skip_tasks:
            _info("step: ensure sshd repair scheduled tasks (SYSTEM)")
            try:
                ensure_scheduled_tasks(dest, args.expected_listen)
            except Exception as exc:  # noqa: BLE001
                _info(f"scheduled tasks error: {exc}")
        if args.register_streamserv:
            _info("step: register StreamServ launcher task")
            try:
                ensure_streamserv_task(dest, args.streamserv_root, args.streamserv_run_as)
            except Exception as exc:  # noqa: BLE001
                _info(f"streamserv task error: {exc}")
        _info("step: write apply spec + install SYSTEM apply-agent (no more admin runs needed)")
        try:
            write_apply_spec(dest, args.expected_listen, args.register_streamserv,
                             args.streamserv_root, args.streamserv_run_as)
            ensure_apply_agent(dest, args.expected_listen)
        except Exception as exc:  # noqa: BLE001
            _info(f"apply-agent error: {exc}")
        _info("")
        _info("== deploy-wre --tasks-only complete ==")
        _info("Future task changes: controller runs host.tasks.apply — no admin needed.")
        return 0

    _info("step: selftest (package source, unstable python path)")
    if not selftest(wre_entry, python_exe):
        _die("selftest failed before deployment; aborting")

    _info(f"step: deploy source to {dest}")
    deploy_source(package_wre_dir, dest)

    if args.keep_existing_policy:
        _info("step: access-policy.json — KEPT existing (--keep-existing-policy)")
    else:
        _info("step: install access-policy.json")
        install_access_policy(dest, args.expected_listen, args.access_token)

    dest_entry = dest / "rpc.py"
    dest_python_exe = python_exe
    _info(f"step: selftest (installed source {dest_entry})")
    if not selftest(dest_entry, dest_python_exe):
        _die("selftest failed after deploy; manual review needed")

    _info("step: backup + rewrite sshd_config (ListenAddress"
          + (" + ForceCommand)" if args.force_command else "; ForceCommand untouched)"))
    sshd_state = backup_and_rewrite_sshd_config(dest, args.expected_listen, args.force_command)
    _info(f"sshd_config result: {sshd_state}")

    if not args.skip_tasks:
        _info("step: ensure scheduled tasks")
        try:
            ensure_scheduled_tasks(dest, args.expected_listen)
        except Exception as exc:  # noqa: BLE001
            _info(f"scheduled tasks error: {exc}")

    if args.register_streamserv:
        _info("step: register StreamServ launcher task")
        try:
            ensure_streamserv_task(dest, args.streamserv_root, args.streamserv_run_as)
        except Exception as exc:  # noqa: BLE001
            _info(f"streamserv task error: {exc}")

    _info("step: write apply spec + install SYSTEM apply-agent")
    try:
        write_apply_spec(dest, args.expected_listen, args.register_streamserv,
                         args.streamserv_root, args.streamserv_run_as)
        ensure_apply_agent(dest, args.expected_listen)
    except Exception as exc:  # noqa: BLE001
        _info(f"apply-agent error: {exc}")

    if not args.no_restart_sshd:
        _info("step: restart sshd")
        try:
            restart_sshd_service(dest)
        except Exception as exc:  # noqa: BLE001
            _info(f"sshd restart error: {exc}")

    _info("")
    _info("== deploy-wre complete ==")
    _info(f"Next step (from the controller):")
    _info(f"  PYTHONPATH=. python3 -m v6.scripts.verify_v6_remote {args.target_name}")
    _info("If ssh cannot reach v6, restore the backup sshd_config in")
    _info(f"  {dest / 'backup'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
