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
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.app_version import APP_VERSION  # noqa: E402


ARCHIVE_ROOT = "deepseek-cowork"
FIXED_ZIP_TIMESTAMP = (2020, 1, 1, 0, 0, 0)
DEFAULT_MAX_DIST_MB = 850
DEFAULT_MAX_ZIP_MB = 375
EDITOR_MAX_UNPACKED_BYTES = 30 * 1024 * 1024
EDITOR_MAX_COMPRESSED_BYTES = 10 * 1024 * 1024
EDITOR_ASSET_PREFIX = "_internal/web/editors/dist/"

QT_TRANSLATION_ALLOWLIST = {
    "qt_en.qm",
    "qt_zh_CN.qm",
    "qtbase_en.qm",
    "qtbase_zh_CN.qm",
}
QTWEBENGINE_LOCALE_ALLOWLIST = {
    "en-GB.pak",
    "en-US.pak",
    "zh-CN.pak",
    "zh-TW.pak",
}

REQUIRED_PATHS = (
    "deepseek-cowork.exe",
    "_internal/python_env/python.exe",
    "_internal/git_bash_env/bin/bash.exe",
    "_internal/PySide6/Qt6WebEngineCore.dll",
    "_internal/PySide6/QtWebEngineCore.pyd",
    "_internal/PySide6/QtWebEngineWidgets.pyd",
    "_internal/PySide6/Qt6Pdf.dll",
    "_internal/PySide6/QtPdf.pyd",
    "_internal/PySide6/QtPdfWidgets.pyd",
    "_internal/PySide6/QtWebChannel.pyd",
    "_internal/PySide6/QtWebEngineProcess.exe",
    "_internal/web/editors/dist/docx.html",
    "_internal/web/editors/dist/docx-editor.js",
    "_internal/web/editors/dist/html.html",
    "_internal/web/editors/dist/html-editor.js",
    "_internal/web/editors/dist/sheet.html",
    "_internal/web/editors/dist/sheet-editor.js",
    "_internal/web/editors/dist/sheet-editor.css",
    "_internal/web/editors/dist/editor.css",
    "_internal/web/editors/dist/THIRD_PARTY_NOTICES.md",
    "_internal/web/editors/dist/THIRD_PARTY_LICENSES.txt",
    "_internal/web/editors/dist/LICENSE-CANVAS-EDITOR.txt",
    "_internal/web/editors/dist/LICENSE-CANVAS-EDITOR-DOCX.txt",
    "_internal/web/editors/dist/LICENSE-UNIVER.txt",
    "_internal/web/editors/dist/LICENSE-BUFFER.txt",
)


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
    if "node_modules" in {part.lower() for part in relative.parts}:
        return "Node development dependency"
    if lowered.startswith(EDITOR_ASSET_PREFIX) and lowered.endswith(".map"):
        return "editor source map"
    if "/__pycache__/" in f"/{lowered}/" or lowered.endswith((".pyc", ".pyo")):
        return "Python cache file"
    if lowered.startswith("_internal/python_env/lib/site-packages/pythonwin/"):
        return "PythonWin IDE runtime"
    if lowered.endswith(".chm"):
        return "compiled help file"
    if lowered.startswith("_internal/pyside6/qml/"):
        return "unused Qt QML tree"
    if lowered.startswith("_internal/pyside6/resources/"):
        if ".debug." in basename.lower() or basename.lower().endswith(".debug"):
            return "Qt debug resource"
    locale_prefix = "_internal/pyside6/translations/qtwebengine_locales/"
    if lowered.startswith(locale_prefix):
        if basename not in QTWEBENGINE_LOCALE_ALLOWLIST:
            return "non-target QtWebEngine locale"
    translation_prefix = "_internal/pyside6/translations/"
    if lowered.startswith(translation_prefix) and not lowered.startswith(locale_prefix):
        if basename not in QT_TRANSLATION_ALLOWLIST:
            return "non-target Qt translation"
    return ""


def _audit_editor_assets(dist_dir: Path, files: list[Path]) -> dict:
    editor_files = [
        relative
        for relative in files
        if relative.as_posix().lower().startswith(EDITOR_ASSET_PREFIX)
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
    if len(parts) >= 2 and parts[0] == "_internal":
        return f"_internal/{parts[1]}"
    return parts[0] if parts else "(root)"


def audit_distribution(dist_dir: Path, max_dist_mb: float = DEFAULT_MAX_DIST_MB) -> dict:
    dist_dir = dist_dir.resolve()
    if not dist_dir.is_dir():
        raise PackageAuditError(f"Distribution directory does not exist: {dist_dir}")

    files = _relative_files(dist_dir)
    paths = {path.as_posix() for path in files}
    missing = [path for path in REQUIRED_PATHS if path not in paths]
    if missing:
        raise PackageAuditError("Missing required packaged files: " + ", ".join(missing))

    forbidden = [
        {"path": path.as_posix(), "reason": reason}
        for path in files
        if (reason := _forbidden_reason(path))
    ]
    if forbidden:
        detail = "; ".join(f"{item['path']} ({item['reason']})" for item in forbidden[:20])
        raise PackageAuditError(f"Forbidden packaged files found: {detail}")

    editor_assets = _audit_editor_assets(dist_dir, files)
    components: dict[str, dict[str, int]] = {}
    total_bytes = 0
    for relative in files:
        size = (dist_dir / relative).stat().st_size
        total_bytes += size
        item = components.setdefault(_component_name(relative), {"bytes": 0, "files": 0})
        item["bytes"] += size
        item["files"] += 1

    limit_bytes = int(float(max_dist_mb) * 1024 * 1024)
    if total_bytes > limit_bytes:
        raise PackageAuditError(
            f"Distribution size {total_bytes / 1024 / 1024:.2f} MB exceeds "
            f"{float(max_dist_mb):.2f} MB limit."
        )

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
        "dist_dir": str(dist_dir),
        "total_bytes": total_bytes,
        "total_mb": round(total_bytes / 1024 / 1024, 2),
        "file_count": len(files),
        "components": ordered_components,
        "required_paths": list(REQUIRED_PATHS),
        "editor_assets": editor_assets,
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
    report_path: Path,
    max_dist_mb: float = DEFAULT_MAX_DIST_MB,
    max_zip_mb: float = DEFAULT_MAX_ZIP_MB,
) -> dict:
    report = audit_distribution(dist_dir, max_dist_mb=max_dist_mb)
    output_path = output_path.resolve()
    candidate_path = output_path.with_name(f".{output_path.name}.candidate-{os.getpid()}")
    candidate_path.unlink(missing_ok=True)
    try:
        report["zip_sha256"] = create_deterministic_zip(dist_dir, candidate_path)
        report["zip_bytes"] = candidate_path.stat().st_size
        report["zip_mb"] = round(report["zip_bytes"] / 1024 / 1024, 2)
        if report["zip_bytes"] > int(float(max_zip_mb) * 1024 * 1024):
            raise PackageAuditError(
                f"Release ZIP size {report['zip_mb']:.2f} MB exceeds "
                f"{float(max_zip_mb):.2f} MB limit."
            )
        os.replace(candidate_path, output_path)
    finally:
        candidate_path.unlink(missing_ok=True)
    report["zip_path"] = str(output_path)

    report_path = report_path.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=ROOT / "dist" / "deepseek-cowork",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "dist" / f"deepseek-cowork-v{APP_VERSION}.zip",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "dist" / "deepseek-cowork-package-report.json",
    )
    parser.add_argument("--max-dist-mb", type=float, default=DEFAULT_MAX_DIST_MB)
    parser.add_argument("--max-zip-mb", type=float, default=DEFAULT_MAX_ZIP_MB)
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
            report = audit_distribution(args.dist_dir, max_dist_mb=args.max_dist_mb)
        else:
            report = package_release(
                args.dist_dir,
                args.output,
                args.report,
                max_dist_mb=args.max_dist_mb,
                max_zip_mb=args.max_zip_mb,
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
        print(f"[OK] Report: {args.report.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
