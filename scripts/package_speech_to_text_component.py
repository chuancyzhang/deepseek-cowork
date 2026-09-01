"""Build or verify the offline Windows x64 speech-to-text release package."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.runtime_components import (  # noqa: E402
    NODE_ARCHIVE,
    NODE_SHA256,
    NODE_VERSION,
    SPEECH_TO_TEXT_ASSETS,
    SPEECH_TO_TEXT_COMPONENT_ID,
    SPEECH_TO_TEXT_NODE_DEPENDENCIES,
    SPEECH_TO_TEXT_NPM_REGISTRY,
    SPEECH_TO_TEXT_PACKAGE_FILENAME,
    SPEECH_TO_TEXT_PACKAGE_MANIFEST,
    SPEECH_TO_TEXT_PACKAGE_PLATFORM,
    SPEECH_TO_TEXT_PACKAGE_SCHEMA,
    _safe_extract,
    _speech_asset_url,
    _speech_to_text_definition_hash,
    _verify_speech_package_archive,
    speech_to_text_skill_runtime_root,
)
from core.process_utils import subprocess_kwargs_no_window  # noqa: E402

import requests  # noqa: E402


FIXED_ZIP_TIMESTAMP = (2020, 1, 1, 0, 0, 0)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()


def _download(
    url: str,
    target: Path,
    expected_size: int | None,
    expected_sha256: str,
    *,
    allow_network: bool,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and _sha256(target) == expected_sha256.lower() and (
        expected_size is None or target.stat().st_size == expected_size
    ):
        return
    if not allow_network:
        raise RuntimeError(f"Offline release cache is missing or invalid: {target}")
    temporary = target.with_suffix(target.suffix + ".part")
    with requests.get(url, stream=True, timeout=(15, 120), headers={"User-Agent": "deepseek-cowork-release-builder"}) as response:
        response.raise_for_status()
        with temporary.open("wb") as handle:
            for chunk in response.iter_content(1024 * 1024):
                if chunk:
                    handle.write(chunk)
    if expected_size is not None and temporary.stat().st_size != expected_size:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"Downloaded size mismatch: {target.name}")
    actual = _sha256(temporary)
    if actual != expected_sha256.lower():
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"Downloaded SHA-256 mismatch: {target.name}")
    os.replace(temporary, target)


def _write_deterministic_zip(source_root: Path, output: Path) -> None:
    files = sorted(
        (path for path in source_root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(source_root).as_posix().casefold(),
    )
    records = [
        {
            "path": path.relative_to(source_root).as_posix(),
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in files
        if path.relative_to(source_root).as_posix() != SPEECH_TO_TEXT_PACKAGE_MANIFEST
    ]
    manifest = {
        "schema": SPEECH_TO_TEXT_PACKAGE_SCHEMA,
        "component_id": SPEECH_TO_TEXT_COMPONENT_ID,
        "platform": SPEECH_TO_TEXT_PACKAGE_PLATFORM,
        "definition_hash": _speech_to_text_definition_hash(),
        "node_version": NODE_VERSION,
        "node_dependencies": SPEECH_TO_TEXT_NODE_DEPENDENCIES,
        "files": records,
    }
    manifest_path = source_root / SPEECH_TO_TEXT_PACKAGE_MANIFEST
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    files = sorted(
        (path for path in source_root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(source_root).as_posix().casefold(),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".part")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            relative = path.relative_to(source_root).as_posix()
            info = zipfile.ZipInfo(relative, FIXED_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    os.replace(temporary, output)


def _copy_dependency_source(source: Path, target: Path) -> None:
    package_path = source / "package.json"
    if not package_path.is_file():
        raise RuntimeError(f"Dependency source does not contain package.json: {source}")
    package = json.loads(package_path.read_text(encoding="utf-8"))
    expected = {
        item.rsplit("@", 1)[0]: item.rsplit("@", 1)[1]
        for item in SPEECH_TO_TEXT_NODE_DEPENDENCIES
    }
    if dict(package.get("dependencies") or {}) != expected:
        raise RuntimeError("Dependency source package.json does not match pinned versions.")
    for name, version in expected.items():
        dependency_manifest = source / "node_modules" / name / "package.json"
        if not dependency_manifest.is_file():
            raise RuntimeError(f"Dependency source is missing {name}.")
        installed = json.loads(dependency_manifest.read_text(encoding="utf-8"))
        if str(installed.get("version") or "") != version:
            raise RuntimeError(f"Dependency source version mismatch: {name}.")
    shutil.copytree(source, target)


def build_package(
    output: Path,
    cache_dir: Path,
    *,
    offline: bool = False,
    dependency_source: Path | None = None,
) -> Path:
    if sys.platform != "win32":
        raise RuntimeError("Speech release packages must be built on Windows x64.")
    cache_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cowork-speech-release-") as temp_dir:
        stage = Path(temp_dir) / "package"
        assets_dir = stage / "assets"
        for asset_name, spec in SPEECH_TO_TEXT_ASSETS.items():
            cached = cache_dir / spec["filename"]
            _download(
                _speech_asset_url(asset_name, "official"),
                cached,
                int(spec["size"]),
                str(spec["sha256"]),
                allow_network=not offline,
            )
            assets_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cached, assets_dir / spec["filename"])

        node_cached = cache_dir / NODE_ARCHIVE
        _download(
            f"https://nodejs.org/dist/{NODE_VERSION}/{NODE_ARCHIVE}",
            node_cached,
            None,
            NODE_SHA256,
            allow_network=not offline,
        )
        node_release_dir = stage / "node-runtime"
        node_release_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(node_cached, node_release_dir / NODE_ARCHIVE)

        node_extract = Path(temp_dir) / "node"
        node_extract.mkdir()
        with zipfile.ZipFile(node_cached, "r") as archive:
            _safe_extract(archive, str(node_extract))
        node_dirs = [path for path in node_extract.iterdir() if path.is_dir()]
        if len(node_dirs) != 1:
            raise RuntimeError("Downloaded Node.js archive structure is invalid.")
        npm_cmd = node_dirs[0] / "npm.cmd"
        if not npm_cmd.is_file():
            raise RuntimeError("Downloaded Node.js archive does not contain npm.cmd.")
        skill_node = stage / "skill-runtime" / "node"
        if dependency_source is not None:
            _copy_dependency_source(dependency_source, skill_node)
        else:
            skill_node.mkdir(parents=True)
            package_json = {
                "name": "deepseek-cowork-speech-to-text-runtime",
                "private": True,
                "dependencies": {
                    "ffmpeg-static": "5.3.0",
                    "sherpa-onnx-node": "1.12.33",
                },
            }
            (skill_node / "package.json").write_text(
                json.dumps(package_json, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            install_command = [
                str(npm_cmd),
                "install",
                "--prefix",
                str(skill_node),
                "--registry",
                SPEECH_TO_TEXT_NPM_REGISTRY,
                "--no-audit",
                "--no-fund",
            ]
            if offline:
                install_command.append("--offline")
            completed = subprocess.run(
                install_command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=1800,
                **subprocess_kwargs_no_window(),
            )
            if completed.returncode != 0:
                raise RuntimeError((completed.stderr or completed.stdout or "npm install failed").strip())
        _write_deterministic_zip(stage, output)
    verify_package(output)
    return output


def verify_package(package_path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="cowork-speech-verify-") as temp_dir:
        _verify_speech_package_archive(str(package_path), temp_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "dist" / SPEECH_TO_TEXT_PACKAGE_FILENAME,
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=ROOT / ".runtime_downloads",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Require already-downloaded assets and npm cache; never access the network.",
    )
    parser.add_argument(
        "--dependency-source",
        type=Path,
        help="Copy an already-installed node runtime directory containing package.json and node_modules.",
    )
    parser.add_argument("--verify", type=Path, help="Verify an existing package instead of building.")
    args = parser.parse_args()
    if args.verify:
        verify_package(args.verify.resolve())
        print(f"Verified: {args.verify.resolve()}")
        return 0
    dependency_source = args.dependency_source.resolve() if args.dependency_source else None
    if dependency_source is None:
        installed_source = Path(speech_to_text_skill_runtime_root()) / "node"
        if (installed_source / "package.json").is_file() and (installed_source / "node_modules").is_dir():
            dependency_source = installed_source
    try:
        output = build_package(
            args.output.resolve(),
            args.cache_dir.resolve(),
            offline=bool(args.offline),
            dependency_source=dependency_source,
        )
    except Exception as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1
    print(f"Created: {output}")
    print(f"SHA256: {_sha256(output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
