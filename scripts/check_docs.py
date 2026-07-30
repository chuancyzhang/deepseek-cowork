"""Validate Cowork's canonical documentation and compatibility entrypoints."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
APP_VERSION = "5.1.0"

CURRENT_DOCS = (
    ROOT / "README.md",
    ROOT / "README_CN.md",
    ROOT / "docs" / "index.md",
    ROOT / "docs" / "product.md",
    ROOT / "docs" / "technical-design.md",
    ROOT / "docs" / "skill-system.md",
    ROOT / "docs" / "user-guide.md",
    ROOT / "docs" / "guides" / "ai-theme-and-visualize.md",
    ROOT / "docs" / "roadmap.md",
)

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
    "scripts/render_user_guide_screenshots.py",
}

LINK_RE = re.compile(r"!?\[[^\]]*]\(([^)]+)\)")
IMAGE_RE = re.compile(r"!\[[^\]]*]\(([^)]+)\)")
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
VERSION_RE = re.compile(r"\b5\.\d+\.\d+\b")


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


def validate_current_versions() -> list[str]:
    errors: list[str] = []
    for path in CURRENT_DOCS:
        text = _read(path)
        versions = set(VERSION_RE.findall(text))
        stale = sorted(version for version in versions if version != APP_VERSION)
        if stale:
            errors.append(f"{path.relative_to(ROOT)}: stale current version(s): {', '.join(stale)}")
        if APP_VERSION not in versions:
            errors.append(f"{path.relative_to(ROOT)}: does not declare {APP_VERSION}")
    return errors


def validate_legacy_paths_removed() -> list[str]:
    errors: list[str] = []
    for source_name in REMOVED_LEGACY_DOCS:
        if (ROOT / source_name).exists():
            errors.append(f"legacy documentation path must be removed: {source_name}")
    return errors


def validate_product_concepts() -> list[str]:
    errors: list[str] = []
    product = _read(ROOT / "docs" / "product.md")
    technical = _read(ROOT / "docs" / "technical-design.md")
    skill_system = _read(ROOT / "docs" / "skill-system.md")
    readmes = _read(ROOT / "README.md") + "\n" + _read(ROOT / "README_CN.md")

    required_product_terms = (
        "Everything is Tool",
        "AI 设计 UI",
        "经验系统",
        "general-experience",
        "不是模型微调",
        "preview_id + revision",
    )
    for term in required_product_terms:
        if term not in product:
            errors.append(f"docs/product.md: missing product concept {term!r}")

    for term in ("最小循环", "Tool Registry", "tool_call_id", "tool_search", "daemon", "恢复日志"):
        if term not in technical:
            errors.append(f"docs/technical-design.md: missing Agent Loop layer {term!r}")

    for term in ("Tool 是执行面", "experience/entries.jsonl", "SkillChangeEvent", "托管 MCP"):
        if term not in skill_system:
            errors.append(f"docs/skill-system.md: missing runtime concept {term!r}")

    for term in ("Everything is Tool", "AI", "Experience System", "经验系统"):
        if term not in readmes:
            errors.append(f"README parity: missing {term!r}")
    return errors


def validate_screenshot_contract() -> list[str]:
    errors: list[str] = []
    expected = {
        ROOT / "docs" / "user-guide.md": 47,
        ROOT / "docs" / "guides" / "ai-theme-and-visualize.md": 11,
    }
    for path, expected_count in expected.items():
        images = IMAGE_RE.findall(_read(path))
        if len(images) != expected_count:
            errors.append(
                f"{path.relative_to(ROOT)}: expected {expected_count} screenshots, found {len(images)}"
            )
    return errors


def validate_protected_diff() -> list[str]:
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", "diff", "--name-only", "--"],
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
        if name in PROTECTED_FILES or name.startswith(PROTECTED_PREFIXES):
            errors.append(f"protected path changed: {name}")
    return errors


def markdown_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "README_CN.md"]
    files.extend(sorted((ROOT / "docs").rglob("*.md")))
    return files


def run_checks(include_protected_diff: bool = True) -> list[str]:
    errors: list[str] = []
    files = markdown_files()
    errors.extend(validate_local_links(files))
    errors.extend(validate_current_versions())
    errors.extend(validate_legacy_paths_removed())
    errors.extend(validate_product_concepts())
    errors.extend(validate_screenshot_contract())
    if include_protected_diff:
        errors.extend(validate_protected_diff())
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-protected-diff",
        action="store_true",
        help="Skip the git diff check for protected runtime and screenshot files.",
    )
    args = parser.parse_args()
    errors = run_checks(include_protected_diff=not args.skip_protected_diff)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"Documentation checks passed for app version {APP_VERSION}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
