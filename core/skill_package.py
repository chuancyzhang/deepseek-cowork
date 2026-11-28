"""Shared validation and resource copying for authored and imported skill packages."""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil

MAX_PACKAGE_BYTES = 50 * 1024 * 1024
MAX_PACKAGE_FILES = 2000


def is_valid_skill_name(skill_name):
    name = str(skill_name or "")
    return bool(re.fullmatch(r"[^\W_][\w-]{0,127}", name)) and not re.fullmatch(
        r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", name
    )


def package_path(root, relative):
    value = str(relative or "").replace("\\", "/")
    parts = value.split("/")
    if not value or any(not part or part in {".", ".."} or part.endswith((".", " "))
                        or re.search(r'[<>:"|?*\x00-\x1f]', part)
                        or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
                        for part in parts):
        raise ValueError(f"Invalid skill resource path: {value}")
    root = Path(root).resolve()
    target = root.joinpath(*parts)
    if not target.resolve().is_relative_to(root):
        raise ValueError(f"Skill resource leaves package: {value}")
    return target


def file_digest(path):
    with open(path, "rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def package_digest(root):
    """Hash bounded package contents, excluding VCS and generated environments."""
    return _collect_package(root, hash_contents=True)


def stage_skill_package(source, destination, *, expected_sha256=""):
    """Copy the package once, checking an explicitly supplied digest against those bytes."""
    digest = _collect_package(source, destination=destination, hash_contents=bool(expected_sha256))
    if expected_sha256 and digest != expected_sha256:
        raise ValueError("Skill source changed since inspection.")


def _collect_package(root, *, destination=None, hash_contents=False):
    from core.skill_adapter import EXCLUDED_DIRS
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("Source is not a directory")
    destination = Path(destination) if destination is not None else None
    digest = hashlib.sha256() if hash_contents else None
    total = count = 0
    seen = set()

    def raise_walk_error(error):
        raise error

    for directory, dirs, files in os.walk(root, onerror=raise_walk_error, followlinks=False):
        dirs[:] = sorted(name for name in dirs if name not in EXCLUDED_DIRS)
        files = sorted(name for name in files if name not in EXCLUDED_DIRS)
        for name in dirs + files:
            child = Path(directory) / name
            if child.is_symlink() or (hasattr(child, "is_junction") and child.is_junction()):
                raise ValueError(f"Skill package contains a link: {child.relative_to(root)}")
        if destination is not None:
            (destination / Path(directory).relative_to(root)).mkdir()
        for name in files:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            package_path(root, relative)
            if relative.casefold() in seen:
                raise ValueError(f"Duplicate skill resource: {relative}")
            seen.add(relative.casefold())
            count += 1
            size = path.stat().st_size
            remaining = MAX_PACKAGE_BYTES - total
            if count > MAX_PACKAGE_FILES or size > remaining:
                raise ValueError("Skill package exceeds 2000 files or 50 MiB.")
            if destination is not None:
                with path.open("rb") as handle:
                    data = handle.read(remaining + 1)
                size = len(data)
                if size > remaining:
                    raise ValueError("Skill package exceeds 50 MiB.")
                target = destination / relative
                target.write_bytes(data)
                shutil.copystat(path, target)
                if digest is not None:
                    contents_hash = hashlib.sha256(data).hexdigest()
            elif digest is not None:
                contents_hash = file_digest(path)
            total += size
            if digest is not None:
                digest.update(relative.encode("utf-8") + b"\0" + contents_hash.encode("ascii") + b"\n")
    return digest.hexdigest() if digest is not None else None


def copy_asset(source, destination, expected_sha256="", *, include_digest=True):
    source = Path(source)
    if not source.is_file() or source.is_symlink() or source.stat().st_size > MAX_PACKAGE_BYTES:
        raise ValueError("Template asset must be a regular file no larger than 50 MiB.")
    # Read once: hash and write the same bytes, even if the original changes later.
    with source.open("rb") as handle:
        data = handle.read(MAX_PACKAGE_BYTES + 1)
    if len(data) > MAX_PACKAGE_BYTES:
        raise ValueError("Template asset exceeds 50 MiB.")
    digest = hashlib.sha256(data).hexdigest() if include_digest or expected_sha256 else None
    if expected_sha256 and digest != expected_sha256:
        raise ValueError("Template asset changed; inspect the current file before saving.")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    metadata = {"size": len(data)}
    if digest is not None:
        metadata["sha256"] = digest
    return metadata


def write_resources(root, resources, *, workspace_dir=None, original_resources=None):
    """Write declared resources into a staging directory; return indexing metadata."""
    if resources is None:
        return []
    if not isinstance(resources, list):
        raise ValueError("resources must be an array.")
    records, seen = [], set()
    for item in resources:
        if not isinstance(item, dict):
            raise ValueError("Each resource must be an object.")
        kind = item.get("kind", "reference")
        prefix = {"reference": "references/", "script": "scripts/", "asset": "assets/"}.get(kind)
        relative = str(item.get("path") or "").replace("\\", "/")
        if not prefix or not relative.startswith(prefix) or relative.casefold() in seen:
            raise ValueError(f"Invalid or duplicate resource: {relative}")
        seen.add(relative.casefold())
        target = package_path(root, relative)
        original = None
        if target.exists():
            if original_resources is None:
                raise ValueError(f"Resource already exists: {relative}")
            original = target.read_bytes()
            if item.get("previous_sha256") and hashlib.sha256(original).hexdigest() != item["previous_sha256"]:
                raise ValueError(f"Resource changed since inspection: {relative}")
        if original_resources is not None:
            original_resources[relative] = original
        result = {"kind": kind, "path": relative, "description": str(item.get("description") or "")}
        if kind == "asset":
            if not workspace_dir:
                raise ValueError("Asset import requires the current workspace.")
            workspace = Path(workspace_dir).resolve()
            source = Path(str(item.get("source_path") or ""))
            source = source if source.is_absolute() else workspace / source
            if not source.resolve().is_relative_to(workspace):
                raise ValueError("Template asset must be in the current workspace.")
            result.update(copy_asset(source, target, str(item.get("sha256") or ""), include_digest=False))
        else:
            content = item.get("content")
            if not isinstance(content, str) or not content.strip():
                raise ValueError(f"Resource has no content: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        records.append(result)
    return records


def index_resources(metadata, records):
    for record in records:
        key = {"reference": "references", "script": "script_refs", "asset": "asset_refs"}[record["kind"]]
        metadata.setdefault(key, [])
        if record["path"] not in metadata[key]:
            metadata[key].append(record["path"])
    return metadata


def validate_package(root, name=None, *, tool_names=None):
    from core.skill_adapter import parse_skill_md_content
    root = Path(root)
    _collect_package(root)
    meta, body = parse_skill_md_content(str(root / "SKILL.md"))
    if not str(meta.get("name") or "").strip() or not str(meta.get("description") or "").strip() or not body.strip():
        raise ValueError("SKILL.md requires name, description and meaningful instructions.")
    if name and meta["name"] != name:
        raise ValueError(f"SKILL.md name '{meta['name']}' does not match '{name}'.")
    metadata_path = root / "skill.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig")) if metadata_path.exists() else {}
    if not isinstance(metadata, dict):
        raise ValueError("skill.json must contain an object.")
    references = []
    for key in ("references", "script_refs", "asset_refs"):
        references.extend(metadata.get(key) or [])
    references.extend(item.get("path", "") for item in metadata.get("script_entries", []) if isinstance(item, dict))
    for text_path in [root / "SKILL.md", *root.glob("references/**/*.md")]:
        text = text_path.read_text(encoding="utf-8-sig")
        for link in re.findall(r"\]\(([^)]+)\)", text):
            link = link.split("#", 1)[0].strip("<>")
            if not link or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", link):
                continue
            # Only package resource links; ordinary example URLs/path prose are not dependencies.
            if link.startswith(("references/", "scripts/", "assets/", "../")):
                linked = (text_path.parent / link).resolve()
                if not linked.is_relative_to(root.resolve()) or not linked.exists():
                    raise ValueError(f"Missing or external skill reference: {link}")
    for relative in references:
        if not isinstance(relative, str) or not package_path(root, relative).is_file():
            raise ValueError(f"Missing skill resource: {relative}")
    trees = {}

    def parse_module(path):
        key = path.resolve()
        if key not in trees:
            trees[key] = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        return trees[key]

    functions = set()
    impl = root / "impl.py"
    if impl.exists():
        tree = parse_module(impl)
        functions = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for path in root.glob("scripts/**/*.py"):
        parse_module(path)
    if tool_names is not None:
        declared_names = {item.get("name") for item in metadata.get("tools", []) if isinstance(item, dict)}
        missing = set(metadata.get("tool_refs") or []) - set(tool_names) - functions - declared_names
        if missing:
            raise ValueError("Unknown tool references: " + ", ".join(sorted(missing)))
    for tool in metadata.get("tools") or []:
        if not isinstance(tool, dict) or not tool.get("name"):
            raise ValueError("Invalid tool declaration.")
        binding = tool.get("binding") or tool.get("runtime_binding") or tool["name"]
        if isinstance(binding, dict):
            binding = binding.get("target") or binding.get("binding") or binding.get("export_name") or tool["name"]
        if not isinstance(binding, str):
            raise ValueError("Invalid tool binding.")
        relative, function = binding.rsplit(":", 1) if ":" in binding else ("impl.py", binding)
        module = package_path(root, relative)
        if not module.is_file():
            raise ValueError(f"Tool binding file is missing: {relative}")
        tree = parse_module(module)
        exports = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        exports.update(alias.asname or alias.name for node in tree.body if isinstance(node, ast.ImportFrom) for alias in node.names)
        if function not in exports:
            raise ValueError(f"Tool binding is missing: {binding}")
    return metadata
