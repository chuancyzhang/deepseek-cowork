"""Prepare, build and package on the target macOS arm64 or UOS V25 x86_64 host."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.app_version import APP_VERSION
from core.platform_capabilities import desktop_target
from core.runtime_manifest import native_runtime_release
from core.update_manifest import release_filename, validate_manifest


def sha256(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(command, **kwargs):
    print("Running:", " ".join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), check=True, **kwargs)


def prepare(target, work, offline=None):
    from core.runtime_components import safe_extract_runtime_tar, TOOLKITS
    runtime_root = work / "runtimes"
    runtime_root.mkdir(parents=True, exist_ok=True)
    for name in ("python", "node", "ripgrep"):
        release = native_runtime_release(name, target)
        target_dir = runtime_root / name
        marker = target_dir / ".release.json"
        expected = {"target": target, "archive_sha256": release.sha256, "url": release.url}
        if marker.is_file() and json.loads(marker.read_text()) == expected:
            run([target_dir / release.executable, "--version"])
            continue
        if target_dir.exists():
            raise RuntimeError(f"Runtime differs from pinned release: {target_dir}. Remove that build-only directory before preparing again.")
        with tempfile.TemporaryDirectory(prefix=f"{name}-", dir=work) as temp:
            temp = Path(temp)
            archive = temp / release.filename
            if offline:
                shutil.copyfile(Path(offline) / release.filename, archive)
            else:
                with urlopen(release.url, timeout=60) as response, archive.open("wb") as stream:
                    shutil.copyfileobj(response, stream)
            if sha256(archive) != release.sha256:
                raise RuntimeError(f"SHA-256 mismatch: {release.filename}")
            extracted = temp / "extracted"
            safe_extract_runtime_tar(archive, extracted)
            roots = list(extracted.iterdir())
            if len(roots) != 1 or not (roots[0] / release.executable).is_file():
                raise RuntimeError(f"Unexpected runtime layout: {release.filename}")
            run([roots[0] / release.executable, "--version"])
            (roots[0] / ".release.json").write_text(json.dumps(expected, indent=2), encoding="utf-8")
            os.replace(roots[0], target_dir)
    python = runtime_root / "python/bin/python3"
    packages = TOOLKITS["documents"]["packages"] + ["requests", "beautifulsoup4"]
    pip_args = ["--no-index", "--find-links", str(Path(offline) / "wheels")] if offline else []
    run([python, "-I", "-m", "pip", "install", "--disable-pip-version-check", *pip_args, *packages])
    run([python, "-I", "-c", "import docx,pptx,openpyxl,xlrd,pypdf,PIL,reportlab,requests,bs4; print('tool runtime ready')"])
    return runtime_root


def build(target, work):
    runtimes = work / "runtimes"
    for name in ("python", "node", "ripgrep"):
        if not (runtimes / name / ".release.json").is_file():
            raise RuntimeError("Run prepare first on this target host.")
    dist = work / "dist"
    run([sys.executable, "-m", "PyInstaller", ROOT / "deepseek-cowork-posix.spec", "--noconfirm", "--clean",
         "--distpath", dist, "--workpath", work / "pyinstaller"], cwd=ROOT)
    app = dist / ("DeepSeek Cowork.app" if target == "macos-arm64" else "deepseek-cowork")
    resource_root = app / ("Contents/Frameworks" if target == "macos-arm64" else "_internal")
    shutil.copytree(runtimes, resource_root / "runtime", symlinks=True, dirs_exist_ok=True)
    for name in ("python", "node", "ripgrep"):
        run([resource_root / "runtime" / name / native_runtime_release(name, target).executable, "--version"])
    if target == "macos-arm64":
        sign_app(app, os.environ.get("COWORK_CODESIGN_IDENTITY") or "-")
    print(app)


def sign_app(app, identity):
    """Sign inner Mach-O files and bundles before the outer application."""
    magic = {b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
             b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}
    entitlements = ROOT / "resources/macos-entitlements.plist"
    options = ["--timestamp", "--options", "runtime"] if identity != "-" else []
    for path in sorted(app.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink():
            continue
        if path.is_file():
            with path.open("rb") as stream:
                macho = stream.read(4) in magic
            if not macho:
                continue
        elif path.suffix not in {".app", ".framework"}:
            continue
        run(["codesign", "--force", "--sign", identity, *options, "--entitlements", entitlements, path])
    run(["codesign", "--force", "--sign", identity, *options, "--entitlements", entitlements, app])
    run(["codesign", "--verify", "--deep", "--strict", app])


def package(target, work, output):
    app = work / "dist" / ("DeepSeek Cowork.app" if target == "macos-arm64" else "deepseek-cowork")
    if not app.is_dir():
        raise RuntimeError("Run build first.")
    notes = (ROOT / "docs/releases" / f"{APP_VERSION}.md").read_text(encoding="utf-8")
    output.mkdir(parents=True, exist_ok=True)
    package_path = output / release_filename(APP_VERSION, target)
    with tempfile.TemporaryDirectory(prefix="package-", dir=work) as temp:
        stage = Path(temp)
        if target == "macos-arm64":
            identity = os.environ.get("COWORK_CODESIGN_IDENTITY")
            profile = os.environ.get("COWORK_NOTARY_PROFILE")
            if not identity or not profile:
                raise RuntimeError("Mac release packaging requires COWORK_CODESIGN_IDENTITY and COWORK_NOTARY_PROFILE.")
            sign_app(app, identity)
            shutil.copytree(app, stage / app.name, symlinks=True)
            os.symlink("/Applications", stage / "Applications")
            run(["hdiutil", "create", "-volname", "DeepSeek Cowork", "-srcfolder", stage,
                 "-format", "UDZO", "-ov", package_path])
            run(["codesign", "--sign", identity, "--timestamp", package_path])
            run(["xcrun", "notarytool", "submit", package_path, "--keychain-profile", profile, "--wait"])
            run(["xcrun", "stapler", "staple", package_path])
            run(["xcrun", "stapler", "validate", package_path])
        else:
            shutil.copytree(app, stage / "opt/deepseek-cowork", symlinks=True)
            control = stage / "DEBIAN"
            control.mkdir()
            (control / "control").write_text(
                f"Package: deepseek-cowork\nVersion: {APP_VERSION}\nArchitecture: amd64\n"
                "Maintainer: DeepSeek Cowork contributors\nSection: utils\nPriority: optional\n"
                "Depends: libc6 (>= 2.28), libgl1, libegl1, libxkbcommon0, libxcb-cursor0, libnss3, libdbus-1-3\n"
                "Recommends: gnome-keyring, fonts-noto-cjk, libreoffice\n"
                "Description: Local desktop agent workspace\n", encoding="utf-8")
            desktop = stage / "usr/share/applications/deepseek-cowork.desktop"
            desktop.parent.mkdir(parents=True)
            desktop.write_text("[Desktop Entry]\nType=Application\nName=DeepSeek Cowork\n"
                               "Exec=/opt/deepseek-cowork/deepseek-cowork\nTerminal=false\nCategories=Office;\n", encoding="utf-8")
            run(["dpkg-deb", "--build", "--root-owner-group", stage, package_path])
    system, arch = target.split("-")
    manifest = validate_manifest(dict(schema_version=1, version=APP_VERSION, target_os=system, arch=arch,
                                     filename=package_path.name, size=package_path.stat().st_size,
                                     sha256=sha256(package_path), notes=notes), target)
    (output / f"latest-{target}.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(package_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "build", "package"))
    parser.add_argument("--offline", type=Path, help="Directory containing pinned archives and wheels/")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    target = desktop_target()
    if target == "windows-x64":
        parser.error("Use the existing Windows build and package scripts on Windows.")
    work = ROOT / ".tmp/native-build" / target
    temp = work / "temp"
    temp.mkdir(parents=True, exist_ok=True)
    os.environ.update(TMPDIR=str(temp), TEMP=str(temp), TMP=str(temp), PIP_CACHE_DIR=str(work / "pip-cache"), PYTHONDONTWRITEBYTECODE="1")
    tempfile.tempdir = str(temp)
    if args.action == "prepare":
        prepare(target, work, args.offline)
    elif args.action == "build":
        build(target, work)
    else:
        package(target, work, args.output.resolve())


if __name__ == "__main__":
    main()
