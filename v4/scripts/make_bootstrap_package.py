"""Build the WRE v4 Windows bootstrap package on the controller (macOS/Linux).

Output: `v4/release/wre-v<version>-windows-x64.zip` containing:
    python/               <- standalone Windows python (embeddable + wheels preinstalled)
        python.exe
        pythonw.exe
        python312._pth
        Lib/site-packages/
        pywin32_system32 DLLs flattened here
    wre/                  <- all native code (rpc.py + actions/ + win32/)
    deploy-wre.py         <- on-host installer
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
V4_ROOT = HERE.parent
NATIVE_SRC = V4_ROOT / "native"
SCRIPTS_SRC = V4_ROOT / "scripts"
RELEASE_DIR = V4_ROOT / "release"
CACHE_DIR = RELEASE_DIR / ".cache"

PYTHON_VERSION = "3.12.7"
PYWIN32_VERSION = "308"
COMTYPES_VERSION = "1.4.8"

PYTHON_EMBEDDABLE_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
PYWIN32_WHEEL_URL = (
    f"https://github.com/mhammond/pywin32/releases/download/b{PYWIN32_VERSION}/"
    f"pywin32-{PYWIN32_VERSION}-win_amd64-py3-none-none.msi"
)
PYWIN32_ALT_URL = PYWIN32_WHEEL_URL
COMTYPES_WHEEL_URL = f"https://files.pythonhosted.org/packages/source/c/comtypes/comtypes-{COMTYPES_VERSION}.tar.gz"

# Use the official pywin32 wheel from PyPI: pywin32-<ver>-cp312-cp312-win_amd64.whl
PYWIN32_PIURL = (
    f"https://files.pythonhosted.org/packages/"
    f"5e/62/pywin32-{PYWIN32_VERSION}-cp312-cp312-win_amd64.whl"
)


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    print(f"[bootstrap] downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "wre-bootstrap/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as fh:  # noqa: S310
        shutil.copyfileobj(resp, fh)
    print(f"[bootstrap] cached -> {dest}")
    return dest


def fetch_python_embeddable() -> Path:
    return download(PYTHON_EMBEDDABLE_URL, CACHE_DIR / f"python-{PYTHON_VERSION}-embed-amd64.zip")


def _fetch_pypi_wheel(package: str, version: str, wheel_pattern: str, cache_name: str) -> Path:
    """Look up a PyPI simple index for a wheel matching `wheel_pattern`."""
    import re
    simple_url = f"https://pypi.org/simple/{package}/"
    print(f"[bootstrap] fetching PyPI simple index: {simple_url}")
    html = urllib.request.urlopen(
        urllib.request.Request(simple_url, headers={"User-Agent": "wre-bootstrap/1.0"}),
        timeout=120,
    ).read().decode("utf-8")  # noqa: S310
    # href URLs contain `#sha256=...` after the wheel filename.
    pattern = rf'href="([^"]+{re.escape(wheel_pattern)})[^"]*"'
    links = re.findall(pattern, html)
    if not links:
        raise RuntimeError(
            f"could not find {wheel_pattern} on {simple_url}; "
            "consider pinning versions or providing a local wheel"
        )
    url = links[0]
    if url.startswith("../../"):
        url = "https://pypi.org/" + url.lstrip("./")
    elif url.startswith("/"):
        url = "https://pypi.org" + url
    elif url.startswith("http://"):
        url = "https" + url[4:]
    return download(url, CACHE_DIR / cache_name)


def fetch_pywin32_wheel() -> Path:
    return _fetch_pypi_wheel(
        "pywin32", PYWIN32_VERSION,
        f"pywin32-{PYWIN32_VERSION}-cp312-cp312-win_amd64.whl",
        f"pywin32-{PYWIN32_VERSION}-cp312-cp312-win_amd64.whl",
    )


def fetch_comtypes_wheel() -> Path:
    return _fetch_pypi_wheel(
        "comtypes", COMTYPES_VERSION,
        f"comtypes-{COMTYPES_VERSION}-py3-none-any.whl",
        f"comtypes-{COMTYPES_VERSION}-py3-none-any.whl",
    )


def extract_zip(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(src) as zf:
        zf.extractall(dest)


def extract_wheel(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(src) as zf:
        zf.extractall(dest)


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
    # Also copy comtypes' gen directory tooling as-is (handled by site setup).
    # Run pywin32 post-install copy step manually: dist to Scripts
    scripts_dir = site_packages / "Scripts"
    if scripts_dir.is_dir():
        # Some pywin32 versions need pywin32_postinstall.py to register class IDs.
        # Embeddable Python skips that step, but com registration only matters for
        # tasks scheduled via the ITaskScheduler IID — which comtypes does lazily.
        pass


def copy_v4_source(dest_wre: Path) -> None:
    # Copy v4 native source alongside python/ (already populated by the build).
    ignore_prefixes = ("__pycache__", ".pytest_cache")
    for entry in NATIVE_SRC.rglob("*"):
        rel = entry.relative_to(NATIVE_SRC)
        if any(part in ignore_prefixes for part in rel.parts):
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
    # C:/CodexRemote/wre/deploy-wre.py.
    wre_target = dest_root / "wre" / "deploy-wre.py"
    shutil.copy2(SCRIPTS_SRC / "deploy_wre.py", wre_target)
    print(f"[bootstrap] copied deploy-wre.py -> {target} and {wre_target}")


def write_manifest(dest_root: Path, version: str) -> None:
    manifest = {
        "name": "wre",
        "version": version,
        "pythonVersion": PYTHON_VERSION,
        "pywin32Version": PYWIN32_VERSION,
        "comtypesVersion": COMTYPES_VERSION,
        "entry": "C:/CodexRemote/wre/rpc.py",
        "pythonExec": "python/pythonw.exe",
    }
    (dest_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build(version: str) -> Path:
    build_dir = V4_ROOT / "build"
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

    copy_v4_source(wre_dest)
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
    parser.add_argument("--version", default="0.4.0")
    args = parser.parse_args(argv)
    build(args.version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())