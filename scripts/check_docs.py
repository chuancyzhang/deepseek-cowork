"""Validate Cowork's canonical documentation and compatibility entrypoints."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from core.app_version import APP_VERSION

CURRENT_DOCS = tuple(ROOT / name for name in (
    "README.md", "README_CN.md", "docs/user-guide.md",
    "docs/technical-design.md", "web/platform/README.md",
))

REMOVED_LEGACY_DOCS = {
    "USER_GUIDE.md",
    "PRODUCT_DOC.md",
    "TECHNICAL_DESIGN.md",
    "SKILL_SYSTEM.md",
    "ROADMAP.md",
    "AI主题与Visualize普通用户指南.md",
    "RELEASE_NOTES_5.0.0.md",
    "RELEASE_NOTES_5.0.3.md",
    "RELEASE_NOTES_5.0.8.md",
}

PROTECTED_PREFIXES = (
    "skills/",
    "ai_skills/",
    "images/",
)
PROTECTED_FILES = {
    "AGENTS.md",
}

# Documentation images may be maintained without allowing edits to app assets.
DOCUMENTATION_IMAGE_PREFIXES = ("images/user-guide/", "images/ai-theme-visualize/")

LINK_RE = re.compile(r"!?\[[^\]]*]\(([^)]+)\)")
IMAGE_RE = re.compile(r"!\[[^\]]*]\(([^)]+)\)")
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
CURRENT_VERSION_RE = re.compile(
    r"^(?:文档适用版本|Documented version)\s*[:：]\s*\*\*(\d+\.\d+\.\d+)\*\*",
    re.MULTILINE,
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _slugify_heading(value: str) -> str:
    value = re.sub(r"`([^`]*)`", r"\1", value)
    value = re.sub(r"\[([^\]]+)]\([^)]+\)", r"\1", value)
    value = re.sub(r"[*_~]", "", value).strip().lower()
    value = re.sub(r"[^\w\u4e00-\u9fff\- ]", "", value)
    return re.sub(r"\s+", "-", value)


def _heading_ids(text: str) -> set[str]:
    counts: dict[str, int] = {}
    result: set[str] = set()
    for heading in HEADING_RE.findall(text):
        base = _slugify_heading(heading)
        count = counts.get(base, 0)
        counts[base] = count + 1
        result.add(base if count == 0 else f"{base}-{count}")
    return result


def validate_local_links(markdown_files: list[Path]) -> list[str]:
    errors: list[str] = []
    heading_cache: dict[Path, set[str]] = {}
    for source in markdown_files:
        text = _read(source)
        for raw_target in LINK_RE.findall(text):
            target = raw_target.strip().strip("<>")
            if not target or target.startswith(("http://", "https://", "mailto:", "cowork-file:")):
                continue
            path_part, _, anchor = target.partition("#")
            target_path = source if not path_part else (source.parent / unquote(path_part)).resolve()
            if not target_path.exists():
                errors.append(f"{source.relative_to(ROOT)}: missing link target {raw_target}")
                continue
            if anchor and target_path.suffix.lower() in {".md", ".mdx"}:
                headings = heading_cache.setdefault(target_path, _heading_ids(_read(target_path)))
                if unquote(anchor).lower() not in headings:
                    errors.append(
                        f"{source.relative_to(ROOT)}: missing heading #{anchor} in "
                        f"{target_path.relative_to(ROOT)}"
                    )
    return errors


def documented_version() -> str:
    path = ROOT / "README_CN.md"
    if not path.is_file():
        return ""
    versions = CURRENT_VERSION_RE.findall(_read(path))
    return versions[0] if len(versions) == 1 else ""


def validate_current_versions() -> list[str]:
    # Guides can stay on a release while source development advances.
    version = documented_version()
    if not version:
        return ["README_CN.md: expected one documented-version declaration"]
    errors = []
    if tuple(map(int, version.split("."))) > tuple(map(int, APP_VERSION.split("."))):
        errors.append(f"documented version {version} is newer than app version {APP_VERSION}")
    if not (ROOT / "docs/releases" / f"{version}.md").is_file():
        errors.append(f"missing release notes for documented version {version}")
    for path in CURRENT_DOCS:
        if not path.is_file():
            errors.append(f"missing guide: {path.relative_to(ROOT)}")
        elif CURRENT_VERSION_RE.findall(_read(path)) != [version]:
            errors.append(f"{path.relative_to(ROOT)}: expected documented version {version}")
    return errors


def validate_legacy_paths_removed() -> list[str]:
    errors: list[str] = []
    for source_name in REMOVED_LEGACY_DOCS:
        if (ROOT / source_name).exists():
            errors.append(f"legacy documentation path must be removed: {source_name}")
    return errors


def validate_document_assets() -> list[str]:
    errors = []
    referenced = set()
    for source in markdown_files():
        for raw_target in IMAGE_RE.findall(_read(source)):
            if not raw_target.startswith(("http://", "https://")):
                referenced.add((source.parent / unquote(raw_target)).resolve())
    for path in (ROOT / "docs").rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT / "docs")
        if path in CURRENT_DOCS or (relative.parts[0] == "releases" and path.suffix == ".md"):
            continue
        if relative.parts[0] == "images" and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"}:
            if path.resolve() not in referenced:
                errors.append(f"unreferenced documentation image: docs/{relative.as_posix()}")
        else:
            errors.append(f"unexpected documentation artifact: docs/{relative.as_posix()}")
    return errors


def validate_protected_diff() -> list[str]:
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", "diff", "HEAD", "--name-only", "--"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return [f"unable to inspect protected diff: {result.stderr.strip()}"]
    errors: list[str] = []
    for raw_name in result.stdout.splitlines():
        name = raw_name.replace("\\", "/").strip()
        doc_image = (name.startswith(DOCUMENTATION_IMAGE_PREFIXES) or name in {"images/english_intro.png", "images/使用界面.png", "images/首页.png"}) and Path(name).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        if name in PROTECTED_FILES or (name.startswith(PROTECTED_PREFIXES) and not doc_image):
            errors.append(f"protected path changed: {name}")
    return errors


def markdown_files() -> list[Path]:
    files = [path for path in CURRENT_DOCS if path.is_file() and path.parent != ROOT / "docs"]
    files.extend(sorted((ROOT / "docs").rglob("*.md")))
    return files


def run_checks(include_protected_diff: bool = True) -> list[str]:
    errors: list[str] = []
    files = markdown_files()
    errors.extend(validate_local_links(files))
    errors.extend(validate_current_versions())
    errors.extend(validate_legacy_paths_removed())
    errors.extend(validate_document_assets())
    if include_protected_diff:
        errors.extend(validate_protected_diff())
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-protected-diff",
        action="store_true",
        help="Skip the git diff check for protected runtime and application assets.",
    )
    args = parser.parse_args()
    errors = run_checks(include_protected_diff=not args.skip_protected_diff)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"Documentation checks passed for documented version {documented_version()}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
