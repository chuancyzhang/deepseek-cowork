"""Audit and create a deterministic DeepSeek Cowork release ZIP."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.app_version import APP_VERSION  # noqa: E402
from web.platform.server.package_policy import (  # noqa: E402
    MAX_DEFAULT_CONFIG_BYTES, validate_default_config,
)
from scripts.bundled_runtime_checks import (  # noqa: E402
    GIT_REQUIRED_FILES, RIPGREP_FILE_HASHES, validate_git_bundle, validate_ripgrep_bundle,
)


ARCHIVE_ROOT = "DeepSeek Cowork-win32-x64"
SERVICE_DIR = "resources/service"
DEFAULT_CONFIG_PATH = SERVICE_DIR + "/_internal/config.json"
FIXED_ZIP_TIMESTAMP = (2020, 1, 1, 0, 0, 0)
EDITOR_MAX_UNPACKED_BYTES = 30 * 1024 * 1024
EDITOR_MAX_COMPRESSED_BYTES = 10 * 1024 * 1024
EDITOR_ASSET_PREFIX = "resources/service/_internal/web/editors/dist/"
BROWSER_SKILL_ASSET_PREFIX = "resources/service/_internal/resources/browser_skill/"
BROWSER_SKILL_CLI_ARCHIVE = "bsk-v0.3.3-x86_64-pc-windows-msvc.zip"
BROWSER_SKILL_EXTENSION_ARCHIVE = "browser-skill-extension-v0.3.3-chrome.zip"
BROWSER_SKILL_PINNED_ARTIFACTS = {
    "cli": {
        "version": "0.3.3",
        "archive": BROWSER_SKILL_CLI_ARCHIVE,
        "url": "https://github.com/Tencent/BrowserSkill/releases/download/cli-v0.3.3/bsk-v0.3.3-x86_64-pc-windows-msvc.zip",
    },
    "extension": {
        "version": "0.3.3",
        "archive": BROWSER_SKILL_EXTENSION_ARCHIVE,
        "url": "https://github.com/Tencent/BrowserSkill/releases/download/ext-v0.3.3/browser-skill-extension-v0.3.3-chrome.zip",
    },
}
BROWSER_SKILL_ARTIFACT_HASHES = {
    BROWSER_SKILL_CLI_ARCHIVE: "5ED6AA00E60C0538A637FEBAC6BFBC6A0BD55A11083CD90A987968CFF4BC0E02",
    BROWSER_SKILL_EXTENSION_ARCHIVE: "10EF76E68F352893FA22DD4CCE2F1B96CB90ED957B815D656E13180D07D3C6EA",
}
WECOM_CLI_VERSION = "1.1.0"
WECOM_CLI_SHA256 = "51CCCBA7A9F84E1995C0AB284DD664A2F79E9ABA0C1FF8782AB9B93540297F1B"

REQUIRED_PATHS = (
    "deepseek-cowork.exe",
    "resources/app.asar",
    "resources/service/cowork-service.exe",
    "desktop-release.json",
    "LICENSE-Cowork.txt",
    "LICENSE",
    "LICENSES.chromium.html",
    "resources/service/_internal/enterprise_capabilities.json",
    "resources/service/_internal/python_env/python.exe",
    "resources/service/_internal/python_env/Lib/site-packages/openpyxl/__init__.py",
    "resources/service/_internal/python_env/Lib/site-packages/xlrd/__init__.py",
    "resources/service/_internal/python_env/Lib/site-packages/docx/__init__.py",
    "resources/service/_internal/python_env/Lib/site-packages/pptx/__init__.py",
    "resources/service/_internal/python_env/Lib/site-packages/PIL/__init__.py",
    "resources/service/_internal/python_env/Lib/site-packages/pypdf/__init__.py",
    "resources/service/_internal/python_env/Lib/site-packages/reportlab/__init__.py",
    *(f"resources/service/_internal/git_bash_env/{name}" for name in GIT_REQUIRED_FILES),
    *(f"resources/service/_internal/ripgrep_env/{name}" for name in RIPGREP_FILE_HASHES),
    "resources/service/_internal/winpty/conpty.dll",
    "resources/service/_internal/winpty/OpenConsole.exe",
    "resources/service/_internal/pywinpty-3.0.5.dist-info/licenses/LICENSE.txt",
    "resources/service/_internal/ai_skills/workbench/SKILL.md",
    "resources/service/_internal/ai_skills/workbench/impl.py",
    "resources/service/_internal/web/editors/dist/terminal.html",
    "resources/service/_internal/web/editors/dist/terminal.js",
    "resources/service/_internal/web/editors/dist/terminal.css",
    "resources/service/_internal/web/editors/dist/browser-automation.js",
    "resources/service/_internal/web/editors/dist/LICENSE-XTERM.txt",
    "resources/service/_internal/web/editors/dist/LICENSE-XTERM-FIT.txt",
    "resources/service/_internal/web/editors/dist/docx.html",
    "resources/service/_internal/web/editors/dist/docx-editor.js",
    "resources/service/_internal/web/editors/dist/docx-review.html",
    "resources/service/_internal/web/editors/dist/docx-review.css",
    "resources/service/_internal/web/editors/dist/docx-review-editor.js",
    "resources/service/_internal/web/editors/dist/html.html",
    "resources/service/_internal/web/editors/dist/html-editor.js",
    "resources/service/_internal/web/editors/dist/sheet.html",
    "resources/service/_internal/web/editors/dist/sheet-editor.js",
    "resources/service/_internal/web/editors/dist/sheet-editor.css",
    "resources/service/_internal/web/editors/dist/editor.css",
    "resources/service/_internal/web/editors/dist/THIRD_PARTY_NOTICES.md",
    "resources/service/_internal/web/editors/dist/THIRD_PARTY_LICENSES.txt",
    "resources/service/_internal/web/editors/dist/LICENSE-CANVAS-EDITOR.txt",
    "resources/service/_internal/web/editors/dist/LICENSE-CANVAS-EDITOR-DOCX.txt",
    "resources/service/_internal/web/editors/dist/LICENSE-UNIVER.txt",
    "resources/service/_internal/web/editors/dist/LICENSE-BUFFER.txt",
    "resources/service/_internal/resources/browser_skill/bundle.json",
    "resources/service/_internal/resources/browser_skill/LICENSE.txt",
    f"resources/service/_internal/resources/browser_skill/artifacts/{BROWSER_SKILL_CLI_ARCHIVE}",
    f"resources/service/_internal/resources/browser_skill/artifacts/{BROWSER_SKILL_EXTENSION_ARCHIVE}",
    "resources/service/_internal/resources/wecom_cli/bundle.json",
    "resources/service/_internal/resources/wecom_cli/LICENSE.txt",
    "resources/service/_internal/resources/wecom_cli/bin/wecom-cli.exe",
    "resources/service/_internal/ai_skills/wecom-unified/SKILL.md",
    "resources/service/_internal/ai_skills/wecom-unified/skill.json",
    "resources/service/_internal/ai_skills/wecom-unified/LICENSE",
)
REQUIRED_PATHS += tuple(path.replace(EDITOR_ASSET_PREFIX, "resources/web/editors/dist/", 1)
                        for path in REQUIRED_PATHS if path.startswith(EDITOR_ASSET_PREFIX))



class PackageAuditError(RuntimeError):
    pass


def _relative_files(dist_dir: Path) -> list[Path]:
    return sorted(
        (path.relative_to(dist_dir) for path in dist_dir.rglob("*") if path.is_file()),
        key=lambda path: path.as_posix().casefold(),
    )


def _forbidden_reason(relative: Path) -> str:
    path = relative.as_posix()
    lowered = path.lower()
    basename = relative.name
    if basename.lower() == "config.json" and path != DEFAULT_CONFIG_PATH:
        return "local configuration outside the shipped default path"
    if ("user_data" in {part.lower() for part in relative.parts}
            or basename.lower().startswith(("connections.sqlite3", "knowledge_library.sqlite3", "enterprise.sqlite3"))
            or basename.lower() == "enterprise_connection.json"
            or basename.lower().startswith("connection_templates")
            or basename.lower() in {"app_variables.json", "app_variables.previous.json"}
            or "connection_locks" in {part.lower() for part in relative.parts}):
        return "local identity, credentials or user data"
    if "node_modules" in {part.lower() for part in relative.parts}:
        return "Node development dependency"
    if lowered.startswith("resources/service/_internal/ai_skills/frontend-slides/"):
        return "Frontend Slides is no longer bundled; rebuild the desktop distribution"
    if "/web/editors/dist/" in "/" + lowered and lowered.endswith(".map"):
        return "editor source map"
    if "/__pycache__/" in f"/{lowered}/" or lowered.endswith((".pyc", ".pyo")):
        return "Python cache file"
    if lowered.startswith("resources/service/_internal/python_env/lib/site-packages/pythonwin/"):
        return "PythonWin IDE runtime"
    if lowered.endswith(".chm"):
        return "compiled help file"
    parts = {part.lower() for part in relative.parts}
    if parts & {"pyside6", "pyqt5", "pyqt6", "shiboken6", "ui"} or basename.lower().startswith("qt6"):
        return "retired Qt runtime or UI"
    if basename.lower() == ".env" or basename.lower().startswith(("chat_history.sqlite", "runtime_journal")):
        return "local runtime data"
    return ""


def _audit_editor_assets(dist_dir: Path, files: list[Path], prefix=EDITOR_ASSET_PREFIX) -> dict:
    editor_files = [
        relative
        for relative in files
        if relative.as_posix().lower().startswith(prefix)
    ]
    unpacked_bytes = sum((dist_dir / relative).stat().st_size for relative in editor_files)
    if unpacked_bytes > EDITOR_MAX_UNPACKED_BYTES:
        raise PackageAuditError(
            f"Editor assets use {unpacked_bytes / 1024 / 1024:.2f} MiB unpacked, "
            f"exceeding the {EDITOR_MAX_UNPACKED_BYTES / 1024 / 1024:.0f} MiB budget."
        )
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(
        archive_buffer,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for relative in editor_files:
            archive.write(dist_dir / relative, relative.as_posix())
    compressed_bytes = len(archive_buffer.getvalue())
    if compressed_bytes > EDITOR_MAX_COMPRESSED_BYTES:
        raise PackageAuditError(
            f"Editor assets use {compressed_bytes / 1024 / 1024:.2f} MiB compressed, "
            f"exceeding the {EDITOR_MAX_COMPRESSED_BYTES / 1024 / 1024:.0f} MiB budget."
        )
    for relative in editor_files:
        if relative.suffix.lower() != ".html":
            continue
        source = (dist_dir / relative).read_text(encoding="utf-8", errors="strict").lower()
        if re.search(r"<(?:script|link)\b[^>]*(?:src|href)\s*=\s*['\"]https?://", source):
            raise PackageAuditError(
                f"Editor HTML has a CDN/runtime network dependency: {relative.as_posix()}"
            )
    return {
        "files": len(editor_files),
        "unpacked_bytes": unpacked_bytes,
        "unpacked_mb": round(unpacked_bytes / 1024 / 1024, 2),
        "compressed_bytes": compressed_bytes,
        "compressed_mb": round(compressed_bytes / 1024 / 1024, 2),
        "unpacked_budget_mb": EDITOR_MAX_UNPACKED_BYTES // 1024 // 1024,
        "compressed_budget_mb": EDITOR_MAX_COMPRESSED_BYTES // 1024 // 1024,
    }


def _component_name(relative: Path) -> str:
    parts = relative.parts
    if len(parts) >= 4 and parts[:3] == ("resources", "service", "_internal"):
        return "/".join(parts[:4])
    return parts[0] if parts else "(root)"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _audit_browser_skill_assets(dist_dir: Path) -> dict:
    bundle_root = dist_dir / SERVICE_DIR / "_internal" / "resources" / "browser_skill"
    manifest_path = bundle_root / "bundle.json"
    license_path = bundle_root / "LICENSE.txt"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise PackageAuditError(f"Invalid BrowserSkill bundle manifest: {exc}") from exc
    if (
        manifest.get("schema") != 1
        or manifest.get("component_id") != "browser-skill"
        or manifest.get("license") != "LICENSE.txt"
    ):
        raise PackageAuditError("BrowserSkill bundle manifest has an unsupported identity or schema.")
    try:
        license_text = license_path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        raise PackageAuditError(f"Invalid BrowserSkill MIT license: {exc}") from exc
    if "MIT License" not in license_text or "Copyright (c) 2026 Tencent" not in license_text:
        raise PackageAuditError("BrowserSkill MIT license is missing or does not match the bundled source.")
    verified = {}
    for artifact_key, pinned in BROWSER_SKILL_PINNED_ARTIFACTS.items():
        archive_name = pinned["archive"]
        expected_sha256 = BROWSER_SKILL_ARTIFACT_HASHES[archive_name]
        declared = manifest.get(artifact_key) or {}
        if any(
            str(declared.get(key) or "").strip().upper()
            != str(value).strip().upper()
            for key, value in {
                "version": pinned["version"],
                "archive": archive_name,
                "url": pinned["url"],
                "sha256": expected_sha256,
            }.items()
        ):
            raise PackageAuditError(
                f"BrowserSkill {artifact_key} manifest entry does not match the pinned release."
            )
        artifact_path = bundle_root / "artifacts" / archive_name
        actual_sha256 = _file_sha256(artifact_path)
        if actual_sha256 != expected_sha256:
            raise PackageAuditError(
                f"BrowserSkill {artifact_key} SHA256 mismatch: "
                f"expected={expected_sha256} actual={actual_sha256}"
            )
        verified[artifact_key] = {
            "archive": archive_name,
            "sha256": actual_sha256,
            "bytes": artifact_path.stat().st_size,
        }
    return verified


def _audit_wecom_cli_assets(dist_dir: Path) -> dict:
    bundle_root = dist_dir / SERVICE_DIR / "_internal" / "resources" / "wecom_cli"
    manifest_path = bundle_root / "bundle.json"
    executable_path = bundle_root / "bin" / "wecom-cli.exe"
    license_path = bundle_root / "LICENSE.txt"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        license_text = license_path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise PackageAuditError(f"Invalid WeCom CLI bundle: {exc}") from exc
    expected = {
        "schema": 1,
        "component_id": "wecom-cli",
        "package": "@wecom/cli-win32-x64",
        "version": WECOM_CLI_VERSION,
        "platform": "win32-x64",
        "archive": "cli-win32-x64-1.1.0.tgz",
        "url": "https://registry.npmjs.org/@wecom/cli-win32-x64/-/cli-win32-x64-1.1.0.tgz",
        "archive_sha256": "1E7F24CCCDC9D61706A717F8765E114663B3911E11B1E22814DEA855AE77D313",
        "executable": "bin/wecom-cli.exe",
        "executable_sha256": WECOM_CLI_SHA256,
        "license": "LICENSE.txt",
    }
    if any(str(manifest.get(key)) != str(value) for key, value in expected.items()):
        raise PackageAuditError("WeCom CLI bundle manifest does not match the pinned release.")
    if "MIT License" not in license_text or "Copyright (c) 2026 WeCom" not in license_text:
        raise PackageAuditError("WeCom CLI MIT license is missing or invalid.")
    actual_sha256 = _file_sha256(executable_path)
    if actual_sha256 != WECOM_CLI_SHA256:
        raise PackageAuditError(
            f"WeCom CLI SHA256 mismatch: expected={WECOM_CLI_SHA256} actual={actual_sha256}"
        )
    return {
        "version": WECOM_CLI_VERSION,
        "sha256": actual_sha256,
        "bytes": executable_path.stat().st_size,
    }


def audit_distribution(dist_dir: Path) -> dict:
    dist_dir = dist_dir.resolve()
    if not dist_dir.is_dir():
        raise PackageAuditError(f"Distribution directory does not exist: {dist_dir}")

    try:
        identity = json.loads((dist_dir / "desktop-release.json").read_text(encoding="utf-8"))
        if identity != {"schema_version": 1, "app_layout": "electron", "target": "windows-x64", "version": APP_VERSION}:
            raise ValueError("layout, target or version mismatch")
    except (OSError, ValueError) as exc:
        raise PackageAuditError(f"Invalid Electron release identity: {exc}") from exc

    files = _relative_files(dist_dir)
    paths = {path.as_posix() for path in files}
    missing = [path for path in REQUIRED_PATHS if path not in paths]
    if missing:
        raise PackageAuditError("Missing required packaged files: " + ", ".join(missing))
    try:
        capabilities = json.loads((dist_dir / "resources/service/_internal/enterprise_capabilities.json").read_text(encoding="utf-8"))
        if capabilities != {"supported_connection_schemas": [1], "supported_platform_api_versions": [1]}:
            raise ValueError()
    except (ValueError, OSError):
        raise PackageAuditError("Invalid enterprise capability declaration in standard package.") from None
    if not any(path.startswith("resources/service/_internal/winpty/_winpty") and path.endswith(".pyd") for path in paths):
        raise PackageAuditError("Missing native pywinpty extension in _internal/winpty.")

    forbidden = [
        {"path": path.as_posix(), "reason": reason}
        for path in files
        if (reason := _forbidden_reason(path))
    ]
    if forbidden:
        detail = "; ".join(f"{item['path']} ({item['reason']})" for item in forbidden[:20])
        raise PackageAuditError(f"Forbidden packaged files found: {detail}")

    default_config = dist_dir / DEFAULT_CONFIG_PATH
    if default_config.exists():
        try:
            with default_config.open("rb") as handle:
                validate_default_config(handle.read(MAX_DEFAULT_CONFIG_BYTES + 1))
        except (ValueError, UnicodeError):
            raise PackageAuditError("resources/service/_internal/config.json is not the empty shipped default; rebuild with the default configuration.") from None

    editor_assets = _audit_editor_assets(dist_dir, files)
    host_editor_assets = _audit_editor_assets(dist_dir, files, "resources/web/editors/dist/")
    service_editors = dist_dir / EDITOR_ASSET_PREFIX
    host_editors = dist_dir / "resources/web/editors/dist"
    if _relative_files(service_editors) != _relative_files(host_editors):
        raise PackageAuditError("Editor resource copies have different file lists.")
    for relative in _relative_files(service_editors):
        if _file_sha256(service_editors / relative) != _file_sha256(host_editors / relative):
            raise PackageAuditError(f"Editor resource copies differ: {relative}")
    browser_skill_assets = _audit_browser_skill_assets(dist_dir)
    wecom_cli_assets = _audit_wecom_cli_assets(dist_dir)
    try:
        git_runtime = validate_git_bundle(dist_dir / "resources/service/_internal/git_bash_env")
        ripgrep_runtime = validate_ripgrep_bundle(dist_dir / "resources/service/_internal/ripgrep_env")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise PackageAuditError(str(exc)) from exc
    components: dict[str, dict[str, int]] = {}
    total_bytes = 0
    for relative in files:
        size = (dist_dir / relative).stat().st_size
        total_bytes += size
        item = components.setdefault(_component_name(relative), {"bytes": 0, "files": 0})
        item["bytes"] += size
        item["files"] += 1

    ordered_components = [
        {
            "name": name,
            "bytes": values["bytes"],
            "size_mb": round(values["bytes"] / 1024 / 1024, 2),
            "files": values["files"],
        }
        for name, values in sorted(
            components.items(),
            key=lambda item: (-item[1]["bytes"], item[0].casefold()),
        )
    ]
    return {
        "app_version": APP_VERSION,
        "app_layout": "electron",
        "supported_connection_schemas": [1],
        "supported_platform_api_versions": [1],
        "dist_dir": str(dist_dir),
        "total_bytes": total_bytes,
        "total_mb": round(total_bytes / 1024 / 1024, 2),
        "file_count": len(files),
        "components": ordered_components,
        "required_paths": list(REQUIRED_PATHS),
        "editor_assets": editor_assets,
        "host_editor_assets": host_editor_assets,
        "browser_skill_assets": browser_skill_assets,
        "wecom_cli_assets": wecom_cli_assets,
        "git_runtime": git_runtime,
        "ripgrep_runtime": ripgrep_runtime,
    }


def _zip_info(archive_name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(archive_name, date_time=FIXED_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    info.create_system = 3
    return info


def create_deterministic_zip(dist_dir: Path, output_path: Path) -> str:
    dist_dir = dist_dir.resolve()
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        dir=output_path.parent,
    )
    os.close(handle)
    temp_path = Path(temp_name)
    try:
        with zipfile.ZipFile(
            temp_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            allowZip64=True,
        ) as archive:
            for relative in _relative_files(dist_dir):
                archive_name = str(PurePosixPath(ARCHIVE_ROOT, *relative.parts))
                with (dist_dir / relative).open("rb") as source:
                    with archive.open(_zip_info(archive_name), "w", force_zip64=True) as destination:
                        shutil.copyfileobj(source, destination, length=1024 * 1024)
        os.replace(temp_path, output_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

    digest = hashlib.sha256()
    with output_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_release(
    dist_dir: Path,
    output_path: Path,
    report_path: Path | None = None,
) -> dict:
    if output_path.name != f"cowork-desktop-v{APP_VERSION}-windows-x64.zip":
        raise PackageAuditError(f"Release filename must be cowork-desktop-v{APP_VERSION}-windows-x64.zip")
    notes_path = ROOT / "docs" / "releases" / f"{APP_VERSION}.md"
    if not notes_path.is_file():
        raise PackageAuditError(f"Release notes are required: {notes_path}")
    notes = notes_path.read_text(encoding="utf-8")
    report = audit_distribution(dist_dir)
    output_path = output_path.resolve()
    candidate_path = output_path.with_name(f".{output_path.name}.candidate-{os.getpid()}")
    candidate_path.unlink(missing_ok=True)
    try:
        report["zip_sha256"] = create_deterministic_zip(dist_dir, candidate_path)
        report["zip_bytes"] = candidate_path.stat().st_size
        report["zip_mb"] = round(report["zip_bytes"] / 1024 / 1024, 2)
        os.replace(candidate_path, output_path)
    finally:
        candidate_path.unlink(missing_ok=True)
    report["zip_path"] = str(output_path)
    manifest = {"schema_version": 1, "app_layout": "electron", "version": APP_VERSION, "target_os": "windows", "arch": "x64",
                                  "filename": output_path.name, "size": report["zip_bytes"],
                                  "sha256": report["zip_sha256"], "notes": notes}
    manifest_path = output_path.with_name("latest-desktop-windows-x64.json")
    handle, temporary = tempfile.mkstemp(prefix=".latest-", suffix=".tmp", dir=output_path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, manifest_path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    report["update_manifest_path"] = str(manifest_path)

    if report_path is not None:
        report_path = report_path.resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=ROOT / "desktop/out" / ARCHIVE_ROOT,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "desktop/out" / f"cowork-desktop-v{APP_VERSION}-windows-x64.zip",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Optional audit report path; routine packaging only emits the ZIP and update manifest.",
    )
    parser.add_argument(
        "--audit-only",
        action="store_true",
        help="Validate the existing distribution without creating a ZIP.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.audit_only:
            report = audit_distribution(args.dist_dir)
        else:
            report = package_release(
                args.dist_dir,
                args.output,
                args.report,
            )
    except PackageAuditError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    print(f"[OK] Distribution: {report['total_mb']:.2f} MB / {report['file_count']} files")
    for component in report["components"][:12]:
        print(
            f"  {component['name']}: {component['size_mb']:.2f} MB "
            f"({component['files']} files)"
        )
    if not args.audit_only:
        print(f"[OK] ZIP: {report['zip_mb']:.2f} MB")
        print(f"[OK] SHA-256: {report['zip_sha256']}")
        if args.report is not None:
            print(f"[OK] Report: {args.report.resolve()}")
        print(f"[OK] Update manifest: {report['update_manifest_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
