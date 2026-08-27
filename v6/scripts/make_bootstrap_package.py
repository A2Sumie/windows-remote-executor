"""Build the WRE v6 Windows bootstrap package on the controller (macOS/Linux).

Output: `v6/release/wre-v<version>-windows-x64.zip` containing:
    python/               <- standalone Windows python (embeddable + wheels preinstalled)
        python.exe
        pythonw.exe
        python312._pth
        Lib/site-packages/
        pywin32_system32 DLLs flattened here
    wre/                  <- all native code (rpc.py + actions/ + win32/)
    deploy-wre.py         <- on-host installer

Supply chain (v5, audit A4): every download is pinned to a sha256 below and
verified after download (cache hits are verified too). Pins were recorded
2026-08-18 from python.org (computed over the downloaded artifact) and the
PyPI simple index `#sha256=` fragments. Archives are extracted through
`_extractall_safe` (PEP 706 `filter="data"` on Python 3.12–3.13; the parameter
was removed in 3.14 where name sanitization is unconditional) to block
zip-slip member names.

Python note: 3.12.10 is the FINAL 3.12 release with Windows binaries on
python.org — 3.12.11+ are source-only security releases (PEP 693 security
mode), so no embed-amd64.zip exists for them. Pinned 2026-08-18 after
checking https://www.python.org/ftp/python/3.12.x/ listings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
V6_ROOT = HERE.parent
NATIVE_SRC = V6_ROOT / "native"
SCRIPTS_SRC = V6_ROOT / "scripts"
RELEASE_DIR = V6_ROOT / "release"
CACHE_DIR = RELEASE_DIR / ".cache"
# v6.0 ships the same pinned interpreter/wheels as v5; reuse the already
# hash-verified v5 cache when v6's own cache is empty (pins re-verified on use).
V5_CACHE_DIR = V6_ROOT.parent / "v5" / "release" / ".cache"

PYTHON_VERSION = "3.12.10"
PYWIN32_VERSION = "308"
COMTYPES_VERSION = "1.4.8"

PYTHON_EMBEDDABLE_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"

# sha256 pins (see module docstring for provenance).
PYTHON_EMBEDDABLE_SHA256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
PYWIN32_WHEEL_SHA256 = "00b3e11ef09ede56c6a43c71f2d31857cf7c54b0ab6e78ac659497abd2834f47"
COMTYPES_WHEEL_SHA256 = "773109b12aa0bec630d5b2272dd983cbaa25605a12fc1319f99730c9d0b72f79"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path, expected_sha256: str) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not (dest.is_file() and dest.stat().st_size > 0):
        v5_hit = V5_CACHE_DIR / dest.name
        if v5_hit.is_file() and _sha256(v5_hit) == expected_sha256:
            shutil.copy2(v5_hit, dest)
            print(f"[bootstrap] reused v5 cache: {dest.name}")
    if not (dest.is_file() and dest.stat().st_size > 0):
        print(f"[bootstrap] downloading {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "wre-bootstrap/5.0"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as fh:  # noqa: S310
            shutil.copyfileobj(resp, fh)
        print(f"[bootstrap] cached -> {dest}")
    actual = _sha256(dest)
    if actual != expected_sha256:
        dest.unlink(missing_ok=True)  # drop the suspect artifact; next run refetches
        raise RuntimeError(
            f"sha256 mismatch for {dest.name}: expected {expected_sha256}, got {actual}. "
            "Do NOT proceed — either the pin is stale or the artifact changed upstream."
        )
    print(f"[bootstrap] sha256 ok: {dest.name} {actual[:16]}...")
    return dest


def fetch_python_embeddable() -> Path:
    return download(
        PYTHON_EMBEDDABLE_URL,
        CACHE_DIR / f"python-{PYTHON_VERSION}-embed-amd64.zip",
        PYTHON_EMBEDDABLE_SHA256,
    )


def _fetch_pypi_wheel(package: str, version: str, wheel_pattern: str,
                      cache_name: str, expected_sha256: str) -> Path:
    """Look up a PyPI simple index for a wheel matching `wheel_pattern`,
    cross-check its `#sha256=` fragment against the pin, then download+verify."""
    import re
    simple_url = f"https://pypi.org/simple/{package}/"
    print(f"[bootstrap] fetching PyPI simple index: {simple_url}")
    html = urllib.request.urlopen(
        urllib.request.Request(simple_url, headers={"User-Agent": "wre-bootstrap/5.0"}),
        timeout=120,
    ).read().decode("utf-8")  # noqa: S310
    # href URLs contain `#sha256=...` after the wheel filename.
    pattern = rf'href="([^"]+{re.escape(wheel_pattern)}[^"]*)"'
    links = re.findall(pattern, html)
    if not links:
        raise RuntimeError(
            f"could not find {wheel_pattern} on {simple_url}; "
            "consider pinning versions or providing a local wheel"
        )
    url = links[0]
    fragment_sha = ""
    if "#sha256=" in url:
        url, fragment_sha = url.rsplit("#sha256=", 1)
        if fragment_sha != expected_sha256:
            raise RuntimeError(
                f"PyPI index sha256 for {wheel_pattern} is {fragment_sha}, but the pin in "
                f"{__file__} says {expected_sha256}. Investigate before updating the pin."
            )
    if url.startswith("../../"):
        url = "https://pypi.org/" + url.lstrip("./")
    elif url.startswith("/"):
        url = "https://pypi.org" + url
    elif url.startswith("http://"):
        url = "https" + url[4:]
    return download(url, CACHE_DIR / cache_name, expected_sha256)


def fetch_pywin32_wheel() -> Path:
    return _fetch_pypi_wheel(
        "pywin32", PYWIN32_VERSION,
        f"pywin32-{PYWIN32_VERSION}-cp312-cp312-win_amd64.whl",
        f"pywin32-{PYWIN32_VERSION}-cp312-cp312-win_amd64.whl",
        PYWIN32_WHEEL_SHA256,
    )


def fetch_comtypes_wheel() -> Path:
    return _fetch_pypi_wheel(
        "comtypes", COMTYPES_VERSION,
        f"comtypes-{COMTYPES_VERSION}-py3-none-any.whl",
        f"comtypes-{COMTYPES_VERSION}-py3-none-any.whl",
        COMTYPES_WHEEL_SHA256,
    )


def _extractall_safe(zf: zipfile.ZipFile, dest: Path) -> None:
    """Zip-slip-safe extractall across controller Python versions.

    - 3.12 / 3.13: PEP 706 `filter="data"` raises on absolute/parent members.
    - >= 3.14: the `filter` parameter was removed; extraction always applies
      the built-in member-name sanitization (see ZipFile.extract note).
    - < 3.12: no filter support; same built-in sanitization, with a warning.
    """
    import inspect
    import sys
    if "filter" in inspect.signature(zipfile.ZipFile.extractall).parameters:
        zf.extractall(dest, filter="data")
    else:
        if sys.version_info < (3, 12):
            print("[bootstrap] WARNING: controller Python < 3.12; extracting "
                  "without filter='data' (built-in name sanitization only)")
        zf.extractall(dest)


def extract_zip(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(src) as zf:
        _extractall_safe(zf, dest)


def extract_wheel(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(src) as zf:
        _extractall_safe(zf, dest)


def enable_site_packages(python_dir: Path) -> None:
    """Append `import site` and `Lib/site-packages` to `python312._pth` so the
    embeddable interpreter picks up our pre-installed packages."""
    pth = python_dir / f"python3{PYTHON_VERSION.split('.')[1]}._pth"
    if not pth.is_file():
        # find the _pth file dynamically
        cands = list(python_dir.glob("python3*._pth"))
        if not cands:
            raise RuntimeError(f"no python312._pth in {python_dir}")
        pth = cands[0]
    original = pth.read_text(encoding="utf-8")
    if original.splitlines()[:1] == ["import site"]:
        new_text = original
    else:
        new_text = "import site\n" + original
    if "Lib/site-packages" not in new_text:
        new_text = new_text.rstrip("\n") + "\nLib/site-packages\n"
    pth.write_text(new_text, encoding="utf-8", newline="\n")
    print(f"[bootstrap] patched {pth.name}")


def flatten_pywin32_dlls(site_packages: Path, python_dir: Path) -> None:
    """Copy pywin32_system32 DLLs next to python.exe — required for COM to load."""
    sys32 = site_packages / "pywin32_system32"
    if sys32.is_dir():
        for dll in sys32.glob("*.dll"):
            shutil.copy2(dll, python_dir / dll.name)
            print(f"[bootstrap] flattened {dll.name} -> {python_dir.name}")


def copy_v6_source(dest_wre: Path) -> None:
    # Copy v6 native source alongside python/ (already populated by the build).
    # access-policy.json is NEVER shipped: the policy is staged by
    # deploy_sftp.py (or an on-host installer) per target — a packaged policy
    # would bake a local token hash into every install.
    ignore_prefixes = ("__pycache__", ".pytest_cache")
    ignore_files = ("access-policy.json",)
    for entry in NATIVE_SRC.rglob("*"):
        rel = entry.relative_to(NATIVE_SRC)
        if any(part in ignore_prefixes for part in rel.parts):
            continue
        if entry.is_file() and entry.name in ignore_files:
            continue
        target = dest_wre / rel
        if entry.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(entry, target)
    print(f"[bootstrap] copied native source -> {dest_wre}")


def copy_deploy_script(dest_root: Path) -> None:
    target = dest_root / "deploy-wre.py"
    shutil.copy2(SCRIPTS_SRC / "deploy_wre.py", target)
    # Also copy into wre/ so light SFTP deploys can run it in-place from
    # C:/WRE/wre/deploy-wre.py.
    wre_target = dest_root / "wre" / "deploy-wre.py"
    shutil.copy2(SCRIPTS_SRC / "deploy_wre.py", wre_target)
    # One-command installer wrapper (self-elevating, sshd check, arg contract):
    ps1 = dest_root / "install-wre.ps1"
    shutil.copy2(SCRIPTS_SRC / "install_wre_template.ps1", ps1)
    print(f"[bootstrap] copied deploy-wre.py -> {target} and {wre_target}; installer -> {ps1}")


def write_manifest(dest_root: Path, version: str) -> None:
    manifest = {
        "name": "wre",
        "version": version,
        "build": "v6",
        "pythonVersion": PYTHON_VERSION,
        "pywin32Version": PYWIN32_VERSION,
        "comtypesVersion": COMTYPES_VERSION,
        "sha256": {
            "pythonEmbeddable": PYTHON_EMBEDDABLE_SHA256,
            "pywin32Wheel": PYWIN32_WHEEL_SHA256,
            "comtypesWheel": COMTYPES_WHEEL_SHA256,
        },
        "entry": "C:/WRE/wre/rpc.py",  # sidecar deploys override via --entry-root
        "pythonExec": "python/pythonw.exe",
    }
    (dest_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build(version: str) -> Path:
    build_dir = V6_ROOT / "build"
    if build_dir.exists():
        shutil.rmtree(build_dir)
    work_root = build_dir / f"wre-{version}-windows-x64"
    work_root.mkdir(parents=True)

    print("[bootstrap] fetch python embeddable")
    py_zip = fetch_python_embeddable()
    print("[bootstrap] fetch pywin32 wheel")
    pywin32_whl = fetch_pywin32_wheel()
    print("[bootstrap] fetch comtypes wheel")
    comtypes_whl = fetch_comtypes_wheel()

    wre_dest = work_root / "wre"
    python_dir = wre_dest / "python"
    site_packages = python_dir / "Lib" / "site-packages"
    print(f"[bootstrap] extract python embeddable -> {python_dir}")
    extract_zip(py_zip, python_dir)

    print(f"[bootstrap] extract pywin32 wheel -> {site_packages}")
    extract_wheel(pywin32_whl, site_packages)
    print(f"[bootstrap] extract comtypes wheel -> {site_packages}")
    extract_wheel(comtypes_whl, site_packages)

    enable_site_packages(python_dir)
    flatten_pywin32_dlls(site_packages, python_dir)

    copy_v6_source(wre_dest)
    copy_deploy_script(work_root)
    write_manifest(work_root, version)

    release_dir = RELEASE_DIR
    release_dir.mkdir(parents=True, exist_ok=True)
    zip_path = release_dir / f"wre-{version}-windows-x64.zip"
    if zip_path.exists():
        zip_path.unlink()
    print(f"[bootstrap] zipping -> {zip_path}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for root, _dirs, files in os.walk(work_root):
            for name in files:
                full = Path(root) / name
                arc = full.relative_to(work_root)
                zf.write(full, arc.as_posix())
    print(f"[bootstrap] done. {zip_path} ({zip_path.stat().st_size} bytes)")
    return zip_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="6.0.0")
    args = parser.parse_args(argv)
    build(args.version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
