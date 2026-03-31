import ast
import json
import os
import re
import shutil


EXCLUDED_DIRS = {".git", "__pycache__", ".venv", "node_modules", "dist", "build"}
EXECUTABLE_SUFFIXES = {".py", ".sh", ".ps1", ".bat", ".cmd"}
REFERENCE_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml"}


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


def detect_external_skill_format(source_path):
    skill_md_path = os.path.join(source_path, "SKILL.md")
    if _has_cowork_frontmatter(skill_md_path):
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

    if "claude.md" in top_level or ("scripts" in top_level and "skill.md" in top_level):
        return "claude"
    if "scripts" in top_level and "claude" in flattened:
        return "claude"

    return "generic"


def _collect_folder_summary(source_path):
    files = []
    references = []
    original_skill_docs = []
    for root, dirs, filenames in os.walk(source_path):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        rel_root = os.path.relpath(root, source_path)
        for filename in filenames:
            rel_path = os.path.normpath(os.path.join(rel_root, filename)) if rel_root != "." else filename
            files.append(rel_path)
            lower_name = filename.lower()
            ext = os.path.splitext(lower_name)[1]
            if lower_name in {"skill.md", "claude.md", "readme.md", "readme.txt"} or ext in REFERENCE_SUFFIXES:
                references.append(rel_path)
            if lower_name == "skill.md":
                original_skill_docs.append(rel_path)
    return {
        "files": sorted(files)[:200],
        "references": sorted(dict.fromkeys(references))[:50],
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


def _build_skill_md(skill_name, description, tool_refs, source_format, source_basename, references):
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
    body = (
        "# Skill Purpose\n"
        f"{description}\n\n"
        "## When to Use\n"
        f"Use this skill when a user provides a {source_format} skill and we need to adapt it into the Cowork skill system without inventing a separate runtime protocol.\n\n"
        "## When Not to Use\n"
        "Do not treat the imported folder itself as a callable workflow. Use direct tools for execution and keep this skill focused on experience, boundaries, and references.\n\n"
        "## Common Pitfalls\n"
        "Do not assume external scripts are already Cowork tools. Only functions exposed from `impl.py` become callable tools here.\n\n"
        "## Experience / Lessons Learned\n"
        "Record adaptation caveats, interface mismatches, and recovery notes here as the skill evolves.\n\n"
        "## Recommended Workflow\n"
        "1. Read the imported references and original skill instructions.\n"
        "2. Map external concepts into Cowork's tool-plus-experience model.\n"
        "3. Use existing Cowork tools directly unless this skill exposes its own `impl.py` functions.\n"
        "4. Add structured experience entries when new migration patterns are discovered.\n\n"
        "## Recommended Tools\n"
        + ("\n".join(f"- `{tool}`" for tool in tool_refs) if tool_refs else "- No skill-local tools are exposed yet; use existing Cowork tools directly.") + "\n\n"
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


def _build_skill_json(skill_name, description, tool_refs, source_format, source_path, references):
    return {
        "version": 2,
        "name": skill_name,
        "kind": "knowledge",
        "capability_group": "knowledge",
        "description": description,
        "tags": ["imported", "adapted", "external-skill", source_format],
        "triggers": [skill_name.replace("-", " "), f"{source_format} skill", "imported skill"],
        "anti_triggers": ["simple file edit"],
        "references": references[:20],
        "tool_refs": tool_refs,
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
    if resolved_format not in {"cowork", "claude", "openclaw", "generic"}:
        raise ValueError(f"Unsupported source format: {resolved_format}")

    source_basename = os.path.basename(os.path.normpath(source_path))
    skill_name = _slugify_skill_name(skill_name or source_basename)
    shutil.copytree(source_path, target_path)

    if resolved_format == "cowork":
        os.makedirs(os.path.join(target_path, "experience"), exist_ok=True)
        if not os.path.isfile(os.path.join(target_path, "skill.json")):
            description = f"Imported Cowork skill '{source_basename}'."
            tool_refs = _discover_impl_tool_refs(target_path)
            references = _collect_folder_summary(target_path)["references"]
            with open(os.path.join(target_path, "skill.json"), "w", encoding="utf-8") as f:
                json.dump(
                    _build_skill_json(skill_name, description, tool_refs, resolved_format, source_path, references),
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        return {
            "skill_name": skill_name,
            "source_format": resolved_format,
            "message": f"Imported native Cowork skill '{skill_name}'.",
        }

    _preserve_original_skill_doc(target_path)
    summary = _collect_folder_summary(target_path)
    tool_refs = _discover_impl_tool_refs(target_path)
    description = f"Adapted from a {resolved_format} skill folder '{source_basename}' into the Cowork skill system."
    with open(os.path.join(target_path, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(_build_skill_md(skill_name, description, tool_refs, resolved_format, source_basename, summary["references"]))
    with open(os.path.join(target_path, "skill.json"), "w", encoding="utf-8") as f:
        json.dump(
            _build_skill_json(skill_name, description, tool_refs, resolved_format, source_path, summary["references"]),
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
