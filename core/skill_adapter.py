import ast
import json
import os
import re
import shutil
import tempfile
import zipfile
import stat


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


def _parse_frontmatter_value(raw):
    value = str(raw or "").strip()
    if not value:
        return ""
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            return [item.strip().strip("\"'") for item in value[1:-1].split(",") if item.strip()]
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return value


def parse_skill_md_content(path):
    content = _safe_read_text(path, limit=1024 * 1024)
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)", content, re.DOTALL)
    if not match:
        return {}, content
    meta = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = _parse_frontmatter_value(value)
    return meta, match.group(2) or ""


def _metadata_from_skill_md(source_path):
    skill_md_path = os.path.join(source_path, "SKILL.md")
    meta, body = parse_skill_md_content(skill_md_path)
    title, summary = _extract_title_and_summary(body)
    name = meta.get("name") if isinstance(meta.get("name"), str) else ""
    frontmatter_description = meta.get("description") if isinstance(meta.get("description"), str) else ""
    description = frontmatter_description or summary or title
    return {
        "name": name.strip(),
        "description": description.strip(),
        "frontmatter_description": frontmatter_description.strip(),
        "title": title.strip(),
        "body": body,
        "meta": meta,
    }


def _read_openai_agent_yaml(source_path):
    yaml_path = os.path.join(source_path, "agents", "openai.yaml")
    if not os.path.isfile(yaml_path):
        return {}
    text = _safe_read_text(yaml_path, limit=100000)
    result = {}
    current_section = ""
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = _parse_frontmatter_value(value.strip())
        if indent == 0:
            current_section = key
            if value not in ("", None):
                result[key] = value
            else:
                result.setdefault(key, {})
        elif current_section:
            section = result.setdefault(current_section, {})
            if isinstance(section, dict):
                section[key] = value
    return result


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
            if filename == ".skillhub.json":
                continue
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
            if filename.lower() in {"skill.md", "readme.md", "readme.txt"} or ext in REFERENCE_SUFFIXES:
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


def is_skill_source_dir(source_path):
    if not os.path.isdir(source_path):
        return False
    top_level = {name.lower() for name in os.listdir(source_path)}
    if "skill.md" in top_level:
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


def _build_skill_json(skill_name, description, tool_refs, source_path, references, script_refs=None, script_entries=None, asset_refs=None):
    source_meta = _metadata_from_skill_md(source_path)
    agent_yaml = _read_openai_agent_yaml(source_path)
    title = (
        ((agent_yaml.get("interface") or {}).get("display_name") if isinstance(agent_yaml.get("interface"), dict) else "")
        or source_meta.get("title")
        or skill_name.replace("-", " ")
    )
    short_description = ""
    if isinstance(agent_yaml.get("interface"), dict):
        short_description = str(agent_yaml["interface"].get("short_description") or "").strip()
    summary = short_description or description
    normalized_script_entries = (script_entries or [])[:50]
    execution_surface = "skill_script" if normalized_script_entries else ("tool_refs" if tool_refs else "knowledge")
    preferred_script_name = normalized_script_entries[0]["name"] if len(normalized_script_entries) == 1 else ""
    tags = ["agent-skill", "imported"]
    for item in (title, skill_name, os.path.basename(os.path.normpath(source_path)).replace("_", " "), description):
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", item or ""):
            lowered = token.lower()
            if len(lowered) >= 4 and lowered not in tags:
                tags.append(lowered)
    triggers = []
    for item in (skill_name.replace("-", " "), title, summary, description):
        if isinstance(item, str) and item.strip() and item not in triggers:
            triggers.append(item)
    policy = agent_yaml.get("policy") if isinstance(agent_yaml.get("policy"), dict) else {}
    allow_implicit = policy.get("allow_implicit_invocation")
    if isinstance(allow_implicit, str):
        allow_implicit = allow_implicit.strip().lower() not in {"false", "0", "no", "off"}
    allow_implicit_bool = allow_implicit is not False
    if not allow_implicit_bool:
        triggers = []
    dependencies = agent_yaml.get("dependencies") if isinstance(agent_yaml.get("dependencies"), dict) else {}
    yaml_tools = dependencies.get("tools") if isinstance(dependencies.get("tools"), list) else []
    merged_tool_refs = []
    for item in list(tool_refs or []) + [str(tool) for tool in yaml_tools if str(tool or "").strip()]:
        if item not in merged_tool_refs:
            merged_tool_refs.append(item)
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
        "tool_refs": merged_tool_refs,
        "script_refs": (script_refs or [])[:50],
        "script_entries": normalized_script_entries,
        "asset_refs": (asset_refs or [])[:50],
        "execution_surface": execution_surface,
        "prompt_disclosure": "full_on_match",
        "preferred_script_name": preferred_script_name,
        "source_format": "agent_skill",
        "allow_implicit_invocation": allow_implicit_bool,
        "experience_policy": {
            "entry_storage": "experience/entries.jsonl",
            "summary_sync": "frontmatter_experience",
        },
        "disclosure_level_defaults": {
            "default_prompt_level": "brief",
            "include_references": False,
            "include_experience_entries": False,
        },
        "workflow": ["Read the original SKILL.md before using this skill."],
        "creation_hints": {
            "source_path": source_path,
            "source_format": "agent_skill",
            "needs_manual_review": False,
        },
    }


def adapt_skill_directory(source_path, target_path, skill_name=None, source_format="auto"):
    if not os.path.isdir(source_path):
        raise ValueError("Source is not a directory")
    skill_md_path = os.path.join(source_path, "SKILL.md")
    if not os.path.isfile(skill_md_path):
        raise ValueError("Source is not a standard Agent Skill package: SKILL.md is missing.")
    source_meta = _metadata_from_skill_md(source_path)
    original_name = source_meta.get("name")
    description = source_meta.get("frontmatter_description")
    if not original_name:
        raise ValueError("Source SKILL.md must include a frontmatter 'name'.")
    if not description:
        raise ValueError("Source SKILL.md must include a frontmatter 'description'.")

    source_basename = os.path.basename(os.path.normpath(source_path))
    skill_name = _slugify_skill_name(skill_name or original_name or source_basename)
    shutil.copytree(source_path, target_path)

    summary = _collect_folder_summary(target_path)
    tool_refs = _discover_impl_tool_refs(target_path)
    skill_json_path = os.path.join(target_path, "skill.json")
    generated_json = _build_skill_json(
        skill_name,
        description,
        tool_refs,
        source_path,
        summary["references"],
        script_refs=summary["script_refs"],
        script_entries=summary["script_entries"],
        asset_refs=summary["asset_refs"],
    )
    existing_json = {}
    if os.path.isfile(skill_json_path):
        with open(skill_json_path, "r", encoding="utf-8-sig") as f:
            payload = json.load(f)
            if isinstance(payload, dict):
                existing_json = payload
    merged_json = dict(generated_json)
    merged_json.update(existing_json)
    merged_json.setdefault("version", 2)
    merged_json.setdefault("name", skill_name)
    merged_json.setdefault("description", description)
    merged_json.setdefault("source_format", "agent_skill")
    merged_json.setdefault("prompt_disclosure", "full_on_match")
    with open(skill_json_path, "w", encoding="utf-8") as f:
        json.dump(
            merged_json,
            f,
            ensure_ascii=False,
            indent=2,
        )
    os.makedirs(os.path.join(target_path, "experience"), exist_ok=True)
    return {
        "skill_name": skill_name,
        "source_format": "agent_skill",
        "message": f"Installed agent skill '{skill_name}' with original SKILL.md preserved.",
    }


def _extract_zip_to_tempdir(source_path):
    temp_dir = tempfile.mkdtemp(prefix="cowork-agent-skill-")
    try:
        with zipfile.ZipFile(source_path, "r") as archive:
            seen = set()
            total = 0
            for member in archive.infolist():
                parts = member.filename.replace("\\", "/").rstrip("/").split("/")
                if any(not p or p in {".", ".."} or p.endswith((".", " "))
                       or re.search(r'[<>:"|?*\x00-\x1f]', p)
                       or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", p)
                       for p in parts):
                    raise ValueError("ZIP contains unsafe paths")
                key = "/".join(parts).casefold()
                if key in seen or stat.S_ISLNK(member.external_attr >> 16):
                    raise ValueError("ZIP contains duplicate paths or links")
                seen.add(key)
                total += member.file_size
                if len(seen) > 2000 or total > 50 * 1024 * 1024:
                    raise ValueError("ZIP exceeds 2000 files or 50 MB expanded size")
                target_path = os.path.abspath(os.path.join(temp_dir, member.filename))
                if os.path.commonpath([temp_dir, target_path]) != temp_dir:
                    raise ValueError("ZIP contains unsafe paths")
            archive.extractall(temp_dir)
        return temp_dir
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def resolve_agent_skill_source(source_path):
    temp_dir = None
    if os.path.isfile(source_path):
        ext = os.path.splitext(source_path)[1].lower()
        if ext == ".zip":
            temp_dir = _extract_zip_to_tempdir(source_path)
            return _resolve_skill_package_dir(temp_dir), temp_dir
        if ext not in {".md", ".markdown"}:
            raise ValueError("Source file must be a Markdown SKILL.md file or ZIP package.")
        temp_dir = tempfile.mkdtemp(prefix="cowork-agent-skill-md-")
        target = os.path.join(temp_dir, "SKILL.md")
        shutil.copy2(source_path, target)
        return temp_dir, temp_dir
    if os.path.isdir(source_path):
        return _resolve_skill_package_dir(source_path), None
    raise ValueError("Source must be a standard Agent Skill directory, Markdown file, or ZIP package.")


def _resolve_skill_package_dir(root):
    if os.path.isfile(os.path.join(root, "SKILL.md")):
        return root
    entries = [
        entry for entry in os.listdir(root)
        if entry not in {".DS_Store", "__MACOSX"} and not entry.startswith(".")
    ]
    child_dirs = [os.path.join(root, entry) for entry in entries if os.path.isdir(os.path.join(root, entry))]
    if len(child_dirs) == 1:
        return _resolve_skill_package_dir(child_dirs[0])
    raise ValueError("Source is not a standard Agent Skill package: root SKILL.md was not found.")
