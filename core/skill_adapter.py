import ast
import json
import os
import re
import shutil


EXCLUDED_DIRS = {".git", "__pycache__", ".venv", "node_modules", "dist", "build"}
EXECUTABLE_SUFFIXES = {".py", ".sh", ".ps1", ".bat", ".cmd", ".js", ".mjs", ".cjs"}
REFERENCE_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml"}
ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".pdf"}
SCRIPT_RUNTIME_MAP = {
    ".py": "python",
    ".sh": "bash",
    ".ps1": "bash",
    ".bat": "bash",
    ".cmd": "bash",
    ".js": "node",
    ".mjs": "node",
    ".cjs": "node",
}


def _slugify_skill_name(raw_name):
    text = (raw_name or "").strip().lower()
    text = re.sub(r"[^a-z0-9_-]+", "-", text)
    text = text.replace("_", "-")
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "imported-skill"


def _has_cowork_frontmatter(path):
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(2048)
    except Exception:
        return False
    return bool(re.match(r"^---\s*\n.*?\n---\s*", content, re.DOTALL))


def _has_skill_json(source_path):
    return os.path.isfile(os.path.join(source_path, "skill.json"))


def _safe_read_text(path, limit=4000):
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except Exception:
        return ""


def _extract_title_and_summary(text):
    lines = [line.strip() for line in str(text or "").splitlines()]
    title = ""
    body_lines = []
    for line in lines:
        if not line:
            continue
        if not title and line.startswith("#"):
            title = line.lstrip("#").strip()
            continue
        body_lines.append(line)
    summary = " ".join(body_lines).strip()
    if len(summary) > 220:
        summary = summary[:217].rstrip() + "..."
    return title, summary


def _metadata_from_source_docs(source_path):
    skill_md_path = os.path.join(source_path, "SKILL.md")
    claude_md_path = os.path.join(source_path, "CLAUDE.md")
    readme_path = ""
    for filename in ("README.md", "README.txt", "readme.md", "readme.txt"):
        candidate = os.path.join(source_path, filename)
        if os.path.isfile(candidate):
            readme_path = candidate
            break

    primary_path = ""
    if os.path.isfile(skill_md_path):
        primary_path = skill_md_path
    elif os.path.isfile(claude_md_path):
        primary_path = claude_md_path
    else:
        primary_path = readme_path
    content = _safe_read_text(primary_path)
    title, summary = _extract_title_and_summary(content)
    return {
        "title": title,
        "summary": summary,
    }


def _infer_script_runtime(path):
    ext = os.path.splitext((path or "").lower())[1]
    return SCRIPT_RUNTIME_MAP.get(ext, "bash")


def _sanitize_script_name(path):
    rel = (path or "").replace("\\", "/").strip("./")
    stem = os.path.splitext(rel)[0]
    stem = stem.replace("/", "__").replace("\\", "__")
    stem = re.sub(r"[^a-zA-Z0-9_-]+", "-", stem)
    stem = re.sub(r"-{2,}", "-", stem).strip("-")
    return stem or "script"


def _normalize_script_entry(entry):
    if isinstance(entry, str):
        entry = {"path": entry}
    if not isinstance(entry, dict):
        return None
    path = str(entry.get("path") or "").strip().replace("/", os.sep).replace("\\", os.sep)
    if not path:
        return None
    runtime = str(entry.get("runtime") or _infer_script_runtime(path)).strip().lower() or _infer_script_runtime(path)
    description = entry.get("description")
    args_schema = entry.get("args_schema") if isinstance(entry.get("args_schema"), dict) else {}
    default_args = entry.get("default_args")
    if isinstance(default_args, str):
        default_args = [default_args]
    elif not isinstance(default_args, list):
        default_args = []
    normalized = {
        "name": str(entry.get("name") or os.path.splitext(os.path.basename(path))[0]).strip() or os.path.splitext(os.path.basename(path))[0],
        "path": os.path.normpath(path),
        "runtime": runtime,
        "description": description if isinstance(description, str) else "",
        "args_schema": args_schema,
        "default_args": [str(item) for item in default_args if item is not None],
        "entrypoint_style": str(entry.get("entrypoint_style") or "script").strip() or "script",
    }
    return normalized


def discover_skill_artifacts(source_path, declared_script_entries=None, declared_script_refs=None, declared_asset_refs=None, declared_references=None):
    script_entries = []
    by_path = {}
    for item in declared_script_entries or []:
        normalized = _normalize_script_entry(item)
        if not normalized:
            continue
        key = normalized["path"].replace("/", os.sep).replace("\\", os.sep).lower()
        by_path[key] = normalized

    script_refs = []
    asset_refs = []
    references = []
    for item in declared_script_refs or []:
        if isinstance(item, str) and item.strip():
            script_refs.append(os.path.normpath(item.strip()))
    for item in declared_asset_refs or []:
        if isinstance(item, str) and item.strip():
            asset_refs.append(os.path.normpath(item.strip()))
    for item in declared_references or []:
        if isinstance(item, str) and item.strip():
            references.append(os.path.normpath(item.strip()))

    for root, dirs, filenames in os.walk(source_path):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        rel_root = os.path.relpath(root, source_path)
        for filename in filenames:
            rel_path = os.path.normpath(os.path.join(rel_root, filename)) if rel_root != "." else filename
            lower_rel = rel_path.lower()
            ext = os.path.splitext(filename.lower())[1]
            if lower_rel.startswith("scripts" + os.sep) and ext in EXECUTABLE_SUFFIXES:
                script_refs.append(rel_path)
                key = os.path.normpath(rel_path).lower()
                if key not in by_path:
                    by_path[key] = _normalize_script_entry({"path": rel_path})
            if lower_rel.startswith("assets" + os.sep) or ext in ASSET_SUFFIXES:
                asset_refs.append(rel_path)
            if filename.lower() in {"skill.md", "claude.md", "readme.md", "readme.txt"} or ext in REFERENCE_SUFFIXES:
                references.append(rel_path)

    for ref in script_refs:
        key = os.path.normpath(ref).lower()
        if key not in by_path:
            by_path[key] = _normalize_script_entry({"path": ref})

    script_entries = list(by_path.values())
    used_names = set()
    for entry in script_entries:
        name = entry["name"]
        if name not in used_names:
            used_names.add(name)
            continue
        entry["name"] = _sanitize_script_name(entry["path"])
        used_names.add(entry["name"])

    def _dedupe(items):
        seen = set()
        result = []
        for item in items:
            norm = os.path.normpath(item)
            key = norm.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(norm)
        return result

    return {
        "script_refs": _dedupe(script_refs),
        "script_entries": sorted(script_entries, key=lambda item: item["path"]),
        "asset_refs": _dedupe(asset_refs),
        "references": _dedupe(references),
    }


def detect_external_skill_format(source_path):
    skill_md_path = os.path.join(source_path, "SKILL.md")
    if _has_skill_json(source_path):
        return "cowork"

    top_level = {name.lower() for name in os.listdir(source_path)}
    flattened = " ".join(sorted(top_level))

    openclaw_markers = {
        "openclaw.json",
        "openclaw.yaml",
        "openclaw.yml",
        ".openclaw",
        "agents",
        "prompts",
    }
    if openclaw_markers & top_level or "openclaw" in flattened:
        return "openclaw"

    if "claude.md" in top_level:
        return "claude"
    if "scripts" in top_level and "claude" in flattened:
        return "claude"
    if os.path.isfile(skill_md_path):
        return "agent_skill"

    return "generic"


def is_skill_source_dir(source_path):
    if not os.path.isdir(source_path):
        return False
    if _has_skill_json(source_path):
        return True
    top_level = {name.lower() for name in os.listdir(source_path)}
    if "skill.md" in top_level or "claude.md" in top_level:
        return True
    if "scripts" in top_level and ("assets" in top_level or "references" in top_level):
        return True
    return False


def discover_importable_skill_dirs(source_path):
    discovered = []
    seen = set()

    def _walk(path):
        norm = os.path.normcase(os.path.abspath(path))
        if norm in seen:
            return
        seen.add(norm)
        if is_skill_source_dir(path):
            discovered.append(path)
            return
        try:
            entries = sorted(os.listdir(path))
        except Exception:
            return
        for entry in entries:
            if entry in EXCLUDED_DIRS or entry.startswith("."):
                continue
            child = os.path.join(path, entry)
            if os.path.isdir(child):
                _walk(child)

    _walk(source_path)
    return discovered


def _collect_folder_summary(source_path):
    files = []
    references = []
    original_skill_docs = []
    artifacts = discover_skill_artifacts(source_path)
    for root, dirs, filenames in os.walk(source_path):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        rel_root = os.path.relpath(root, source_path)
        for filename in filenames:
            rel_path = os.path.normpath(os.path.join(rel_root, filename)) if rel_root != "." else filename
            files.append(rel_path)
            if filename.lower() == "skill.md":
                original_skill_docs.append(rel_path)
    return {
        "files": sorted(files)[:200],
        "references": artifacts["references"][:50],
        "script_refs": artifacts["script_refs"][:50],
        "script_entries": artifacts["script_entries"][:50],
        "asset_refs": artifacts["asset_refs"][:50],
        "original_skill_docs": sorted(dict.fromkeys(original_skill_docs)),
    }


def _discover_impl_tool_refs(target_path):
    impl_path = os.path.join(target_path, "impl.py")
    if not os.path.isfile(impl_path):
        return []
    try:
        with open(impl_path, "r", encoding="utf-8", errors="replace") as f:
            tree = ast.parse(f.read(), filename=impl_path)
    except Exception:
        return []
    tool_refs = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            tool_refs.append(node.name)
    return tool_refs


def _format_frontmatter_value(value):
    if isinstance(value, list):
        return "[" + ", ".join(json.dumps(item, ensure_ascii=False) for item in value) + "]"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _build_skill_md(skill_name, description, tool_refs, source_format, source_basename, references, script_entries=None):
    script_entries = script_entries or []
    frontmatter = {
        "name": skill_name,
        "description": description,
        "license": "Apache-2.0",
        "type": "ai_generated",
        "created_by": "system",
        "kind": "knowledge",
        "capability_group": "knowledge",
        "experience": [],
    }
    if tool_refs:
        frontmatter["allowed-tools"] = tool_refs
    front_lines = [f"{key}: {_format_frontmatter_value(value)}" for key, value in frontmatter.items()]

    reference_lines = "\n".join(f"- `{ref}`" for ref in references[:12]) if references else "- Review the imported source files directly when needed."
    script_lines = "\n".join(
        f"- `{entry['name']}` -> `{entry['path']}` ({entry['runtime']})" for entry in script_entries[:12]
    ) if script_entries else "- No skill scripts detected."
    body = (
        "# Skill Purpose\n"
        f"{description}\n\n"
        "## When to Use\n"
        f"Use this skill when a user provides a {source_format} skill and we need to adapt it into the Cowork skill system without inventing a separate runtime protocol.\n\n"
        "## When Not to Use\n"
        "Do not treat the imported folder itself as a callable workflow. Use direct tools for execution and keep this skill focused on experience, boundaries, and references.\n\n"
        "## Common Pitfalls\n"
        "Do not assume external scripts are automatically registered as Cowork tools. Skill scripts are executed through sandboxed runtime tools.\n\n"
        "## Experience / Lessons Learned\n"
        "Record adaptation caveats, interface mismatches, and recovery notes here as the skill evolves.\n\n"
        "## Recommended Workflow\n"
        "1. Read the imported references and original skill instructions.\n"
        "2. Map external concepts into Cowork's tool-plus-experience model.\n"
        "3. Use existing Cowork tools directly unless this skill exposes its own `impl.py` functions.\n"
        "4. Add structured experience entries when new migration patterns are discovered.\n\n"
        "## Recommended Tools\n"
        + ("\n".join(f"- `{tool}`" for tool in tool_refs) if tool_refs else "- No skill-local tools are exposed yet; use existing Cowork tools directly.") + "\n\n"
        + "## Skill Scripts\n"
        f"{script_lines}\n\n"
        + "## Interface Details\n"
        f"Source folder: `{source_basename}`\n"
        f"Detected source format: `{source_format}`\n"
        "This adapted skill follows Cowork's experience-package model. The executable surface remains tools.\n\n"
        "## Constraints and Safety Rules\n"
        "Inspect imported code before execution. Keep wrappers minimal. Do not fabricate tool refs for scripts that are not actually registered.\n\n"
        "## References\n"
        f"{reference_lines}\n"
    )
    return f"---\n{chr(10).join(front_lines)}\n---\n\n{body}"


def _build_skill_json(skill_name, description, tool_refs, source_format, source_path, references, script_refs=None, script_entries=None, asset_refs=None):
    source_meta = _metadata_from_source_docs(source_path)
    title = source_meta.get("title") or skill_name.replace("-", " ")
    summary = source_meta.get("summary") or description
    normalized_script_entries = (script_entries or [])[:50]
    execution_surface = "skill_script" if normalized_script_entries else ("tool_refs" if tool_refs else "knowledge")
    preferred_script_name = normalized_script_entries[0]["name"] if len(normalized_script_entries) == 1 else ""
    prompt_disclosure = "full_on_match" if (source_format in {"agent_skill", "claude", "openclaw"} or normalized_script_entries) else "brief_only"
    tags = ["imported", "adapted", "external-skill", source_format]
    for item in (title, os.path.basename(os.path.normpath(source_path)).replace("_", " ")):
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", item or ""):
            lowered = token.lower()
            if len(lowered) >= 4 and lowered not in tags:
                tags.append(lowered)
    triggers = [skill_name.replace("-", " "), title, f"{source_format} skill", "imported skill"]
    if summary and summary not in triggers:
        triggers.append(summary)
    return {
        "version": 2,
        "name": skill_name,
        "kind": "knowledge",
        "capability_group": "knowledge",
        "description": description,
        "tags": tags[:16],
        "triggers": [item for item in triggers if isinstance(item, str) and item.strip()][:8],
        "anti_triggers": ["simple file edit"],
        "references": references[:20],
        "tool_refs": tool_refs,
        "script_refs": (script_refs or [])[:50],
        "script_entries": normalized_script_entries,
        "asset_refs": (asset_refs or [])[:50],
        "execution_surface": execution_surface,
        "prompt_disclosure": prompt_disclosure,
        "preferred_script_name": preferred_script_name,
        "experience_policy": {
            "entry_storage": "experience/entries.jsonl",
            "summary_sync": "frontmatter_experience",
        },
        "disclosure_level_defaults": {
            "default_prompt_level": "brief",
            "include_references": False,
            "include_experience_entries": False,
        },
        "workflow": [
            "Read the imported source guidance first.",
            "Translate the external skill into Cowork's experience-package model.",
            "Use tools directly; only rely on skill-local tools that are actually registered from impl.py.",
        ],
        "creation_hints": {
            "source_path": source_path,
            "source_format": source_format,
            "needs_manual_review": source_format != "cowork",
        },
    }


def _preserve_original_skill_doc(target_path):
    skill_md_path = os.path.join(target_path, "SKILL.md")
    if not os.path.isfile(skill_md_path) or _has_cowork_frontmatter(skill_md_path):
        return
    refs_dir = os.path.join(target_path, "references")
    os.makedirs(refs_dir, exist_ok=True)
    preserved_path = os.path.join(refs_dir, "source-SKILL.md")
    shutil.move(skill_md_path, preserved_path)


def adapt_skill_directory(source_path, target_path, skill_name=None, source_format="auto"):
    if not os.path.isdir(source_path):
        raise ValueError("Source is not a directory")
    resolved_format = (source_format or "auto").lower()
    if resolved_format == "auto":
        resolved_format = detect_external_skill_format(source_path)
    if resolved_format not in {"cowork", "agent_skill", "claude", "openclaw", "generic"}:
        raise ValueError(f"Unsupported source format: {resolved_format}")

    source_basename = os.path.basename(os.path.normpath(source_path))
    skill_name = _slugify_skill_name(skill_name or source_basename)
    shutil.copytree(source_path, target_path)

    summary = _collect_folder_summary(target_path)
    tool_refs = _discover_impl_tool_refs(target_path)

    if resolved_format == "cowork":
        os.makedirs(os.path.join(target_path, "experience"), exist_ok=True)
        if not os.path.isfile(os.path.join(target_path, "skill.json")):
            description = f"Imported Cowork skill '{source_basename}'."
            with open(os.path.join(target_path, "skill.json"), "w", encoding="utf-8") as f:
                json.dump(
                    _build_skill_json(
                        skill_name,
                        description,
                        tool_refs,
                        resolved_format,
                        source_path,
                        summary["references"],
                        script_refs=summary["script_refs"],
                        script_entries=summary["script_entries"],
                        asset_refs=summary["asset_refs"],
                    ),
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        return {
            "skill_name": skill_name,
            "source_format": resolved_format,
            "message": f"Imported native Cowork skill '{skill_name}'.",
        }

    if resolved_format == "agent_skill":
        os.makedirs(os.path.join(target_path, "experience"), exist_ok=True)
        skill_json_path = os.path.join(target_path, "skill.json")
        if not os.path.isfile(skill_json_path):
            description = f"Imported agent skill '{source_basename}' into the Cowork skill system."
            with open(skill_json_path, "w", encoding="utf-8") as f:
                json.dump(
                    _build_skill_json(
                        skill_name,
                        description,
                        tool_refs,
                        resolved_format,
                        source_path,
                        summary["references"],
                        script_refs=summary["script_refs"],
                        script_entries=summary["script_entries"],
                        asset_refs=summary["asset_refs"],
                    ),
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        return {
            "skill_name": skill_name,
            "source_format": resolved_format,
            "message": f"Imported agent skill '{skill_name}' with native SKILL.md preserved.",
        }

    _preserve_original_skill_doc(target_path)
    description = f"Adapted from a {resolved_format} skill folder '{source_basename}' into the Cowork skill system."
    with open(os.path.join(target_path, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(
            _build_skill_md(
                skill_name,
                description,
                tool_refs,
                resolved_format,
                source_basename,
                summary["references"],
                script_entries=summary["script_entries"],
            )
        )
    with open(os.path.join(target_path, "skill.json"), "w", encoding="utf-8") as f:
        json.dump(
            _build_skill_json(
                skill_name,
                description,
                tool_refs,
                resolved_format,
                source_path,
                summary["references"],
                script_refs=summary["script_refs"],
                script_entries=summary["script_entries"],
                asset_refs=summary["asset_refs"],
            ),
            f,
            ensure_ascii=False,
            indent=2,
        )
    os.makedirs(os.path.join(target_path, "experience"), exist_ok=True)
    return {
        "skill_name": skill_name,
        "source_format": resolved_format,
        "message": f"Adapted {resolved_format} skill '{skill_name}' into Cowork format.",
    }
