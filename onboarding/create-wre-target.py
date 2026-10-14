"""Create a private WRE target and a hash-only Windows bootstrap document."""

from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

TAILNET = ipaddress.IPv4Network("100.64.0.0/10")


def tailnet_address(value: str) -> str:
    address = ipaddress.IPv4Address(value)
    if address not in TAILNET:
        raise ValueError("use the Windows host's actual Tailscale IPv4 address")
    return str(address)


def resolve_device(name: str) -> str:
    result = subprocess.run(
        ["tailscale", "status", "--json"], check=True,
        capture_output=True, text=True, timeout=15,
    )
    peers = json.loads(result.stdout).get("Peer", {}).values()
    matches = [peer for peer in peers if name.casefold() in {
        str(peer.get("HostName", "")).casefold(),
        str(peer.get("DNSName", "")).rstrip(".").casefold(),
    }]
    if len(matches) != 1:
        raise ValueError("Tailscale device name must identify exactly one peer")
    if not matches[0].get("Online"):
        raise ValueError("the selected Tailscale peer is offline")
    addresses = [address for address in matches[0].get("TailscaleIPs", []) if ":" not in address]
    if len(addresses) != 1:
        raise ValueError("the selected peer has no unique Tailscale IPv4 address")
    return tailnet_address(addresses[0])


def controller_address() -> str:
    result = subprocess.run(
        ["tailscale", "ip", "-4"], check=True,
        capture_output=True, text=True, timeout=15,
    )
    return tailnet_address(result.stdout.strip())


def read_public_key(path: Path) -> str:
    text = path.read_text(encoding="utf-8").strip()
    fields = text.split()
    if "\n" in text or "\r" in text or len(fields) < 2:
        raise ValueError("the .pub file must contain one OpenSSH public key")
    if not re.fullmatch(r"ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)", fields[0]):
        raise ValueError("the .pub file is not a supported OpenSSH public key")
    base64.b64decode(fields[1], validate=True)
    return text


def read_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = shlex.split(line)
        if len(fields) != 1 or "=" not in fields[0]:
            raise ValueError("the existing target has an invalid env line")
        key, value = fields[0].split("=", 1)
        values[key] = value
    return values


def private_write(path: Path, text: str, *, replace: bool = False) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        if replace:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)  # fail rather than overwrite an existing target
        path.chmod(0o600)
    finally:
        Path(temporary).unlink(missing_ok=True)


def create_target(args: argparse.Namespace) -> dict[str, object]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", args.name):
        raise ValueError("name must be a simple host alias, with no path separators")
    if not args.user or any(character in args.user for character in "\r\n\x00"):
        raise ValueError("user must be the actual Windows SSH account")
    host = tailnet_address(args.host) if args.host is not None else resolve_device(args.tailscale_device)
    controller = tailnet_address(args.controller_address) if args.controller_address else controller_address()
    key = Path(args.key).expanduser().resolve(strict=True)
    if not key.is_file():
        raise ValueError("SSH private key path is not a file")
    public_key = read_public_key(Path(str(key) + ".pub"))
    directory = Path(args.targets_dir).expanduser().resolve()
    target = directory / f"{args.name}.env"
    bootstrap = directory / f"{args.name}.bootstrap.json"
    values = {
        "TARGET_NAME": args.name, "TARGET_HOST": host, "TARGET_USER": args.user.casefold(),
        "TARGET_PORT": "22", "TARGET_KEY": str(key),
        "TARGET_EXPECTED_LISTEN_ADDRESS": host, "TARGET_EXPOSURE_MODE": "private-only",
    }
    if target.exists():
        previous = read_env(target)
        if not (args.reuse or args.rotate_token):
            raise ValueError("target exists; use --reuse or explicitly use --rotate-token")
        if any((previous.get(field, "").casefold() if field == "TARGET_USER" else previous.get(field)) != value
               for field, value in values.items()):
            raise ValueError("existing target details differ; use a separate host alias")
        old_token = previous.get("TARGET_ACCESS_TOKEN", "")
        if args.reuse and len(old_token) < 32:
            raise ValueError("existing target has no usable token; use --rotate-token")
        token = old_token if args.reuse else secrets.token_hex(32)
    else:
        if args.reuse or args.rotate_token:
            raise ValueError("--reuse/--rotate-token requires an existing target")
        token = secrets.token_hex(32)
    values["TARGET_ACCESS_TOKEN"] = token
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    if target.exists() and args.rotate_token:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        private_write(target.with_name(target.name + ".before-rotation-" + stamp),
                      target.read_text(encoding="utf-8"))
    if not args.reuse:
        private_write(target, "".join(f"{key}={shlex.quote(value)}\n" for key, value in values.items()),
                      replace=bool(args.rotate_token))
    else:
        target.chmod(0o600)
    document = {
        "schemaVersion": 1, "name": args.name, "host": host, "user": args.user.casefold(),
        "controllerAddress": controller,
        "publicKey": public_key,
        "tokenSha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
    }
    private_write(bootstrap, json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                  replace=bootstrap.exists())
    return {"privateTargetEnv": str(target), "windowsBootstrap": str(bootstrap),
            "host": host, "tokenPrinted": False, "tokenRotated": bool(args.rotate_token)}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--name", required=True)
    address = result.add_mutually_exclusive_group(required=True)
    address.add_argument("--host", help="Actual Windows Tailscale IPv4 address")
    address.add_argument("--tailscale-device", help="Exact online Tailscale peer hostname")
    result.add_argument("--user", required=True, help="Windows account name, not its profile folder")
    result.add_argument("--key", required=True, help="Existing controller private key; .pub must be beside it")
    result.add_argument("--controller-address", help="Controller Tailscale IPv4; default: tailscale ip -4")
    result.add_argument("--targets-dir", default="~/.config/wre/targets")
    existing = result.add_mutually_exclusive_group()
    existing.add_argument("--reuse", action="store_true", help="Keep an existing private target's token")
    existing.add_argument("--rotate-token", action="store_true", help="Explicitly replace a lost token, saving a private backup")
    return result


def main() -> int:
    try:
        print(json.dumps(create_target(parser().parse_args()), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError, KeyError) as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
