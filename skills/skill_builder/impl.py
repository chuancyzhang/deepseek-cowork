import json
import os
import re
import shutil
import tempfile
import threading
import logging
import inspect

from core.env_utils import get_app_data_dir
from core.skill_adapter import adapt_staged_skill_directory, parse_skill_md_content
from core.skill_install import prepare_source, check_cancel
from core.skill_package import index_resources, is_valid_skill_name, package_path, stage_skill_package, validate_package, write_resources


_SKILL_MUTATION_LOCK = threading.RLock()
log = logging.getLogger("skills.builder")


def _normalize_tools_list(tools_list):
    if tools_list is None:
        return []
    if isinstance(tools_list, str):
        tools_list = json.loads(tools_list)
    if not isinstance(tools_list, list):
        raise ValueError("tools_list must be a list.")
    return tools_list


def _normalize_json_value(value):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _resolve_skill_dir(skill_name, target_scope="auto"):
    if not is_valid_skill_name(skill_name):
        return None, "Error: Skill name must start with a letter or digit, contain only letters, digits, underscores or hyphens, and not be a Windows reserved name (128 characters maximum)."
    app_data_dir = get_app_data_dir()
    ai_skills_dir = os.path.join(app_data_dir, "ai_skills")
    os.makedirs(ai_skills_dir, exist_ok=True)
    builtin_skills_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    ai_path = os.path.join(ai_skills_dir, skill_name)
    builtin_path = os.path.join(builtin_skills_dir, skill_name)
    scope = (target_scope or "auto").lower()
    if scope == "ai_only":
        return ai_path, None
    if scope == "builtin_only":
        return builtin_path, None
    if os.path.isdir(ai_path):
        return ai_path, None
    if os.path.isdir(builtin_path):
        return builtin_path, None
    return ai_path, None


def _publish_skill_change(context, action, skill_name):
    context = context if isinstance(context, dict) else {}
    publisher = context.get("skill_change_publisher")
    if not callable(publisher):
        raise RuntimeError("Skill was written but the runtime change publisher is unavailable.")
    config_manager = context.get("config_manager")
    prior_enabled = None
    if action == "created" and config_manager and hasattr(config_manager, "set_skill_enabled"):
        if hasattr(config_manager, "is_skill_enabled"):
            prior_enabled = config_manager.is_skill_enabled(skill_name, True)
        config_manager.set_skill_enabled(skill_name, True)
    try:
        return publisher(
            {
                "action": action,
                "skill_names": [skill_name],
                "source": "ai",
                "session_id": context.get("session_id") or "",
            }
        )
    except Exception:
        if prior_enabled is not None:
            config_manager.set_skill_enabled(skill_name, prior_enabled)
        raise


def _validate_staged_skill(skill_dir, skill_name, context=None):
    manager = (context or {}).get("skill_manager")
    return validate_package(skill_dir, skill_name, tool_names=set(manager.tools) if manager is not None else None)


def _swap_staged_skill(staging_dir, target_dir):
    backup_dir = ""
    if os.path.exists(target_dir):
        backup_dir = tempfile.mkdtemp(prefix=f".{os.path.basename(target_dir)}-backup-", dir=os.path.dirname(target_dir))
        os.rmdir(backup_dir)
        os.replace(target_dir, backup_dir)
    try:
        os.replace(staging_dir, target_dir)
    except Exception:
        if backup_dir and os.path.exists(backup_dir):
            os.replace(backup_dir, target_dir)
        raise
    return backup_dir


def _rollback_published_skill(target_dir, backup_dir):
    if os.path.isdir(target_dir):
        shutil.rmtree(target_dir)
    if backup_dir and os.path.exists(backup_dir):
        os.replace(backup_dir, target_dir)


def _build_sections(description, usage_guidelines, interface_details=None):
    return str(usage_guidelines or description or "").strip()


def _frontmatter_value(value):
    if isinstance(value, list):
        return "[" + ", ".join([json.dumps(item, ensure_ascii=False) for item in value]) + "]"
    text = str(value)
    if any(char in text for char in ("\n", ":", "#", '"')):
        return json.dumps(text, ensure_ascii=False)
    return text


def _patch_frontmatter(raw, changes):
    # Preserve nested YAML, comments and unknown fields instead of flattening them.
    for key, value in changes.items():
        pattern = re.compile(r"(?m)^" + re.escape(key) + r":[^\n]*(?:\n[ \t]+[^\n]*)*")
        replacement = key + ": " + _frontmatter_value(value)
        raw = pattern.sub(lambda _match: replacement, raw, count=1) if pattern.search(raw) else raw.rstrip() + "\n" + replacement
    return raw


def _build_skill_md(skill_name, description, tool_refs, usage_guidelines, description_cn=None, kind="knowledge", created_by="ai", capability_group=None):
    frontmatter = {
        "name": skill_name,
        "description": description or "No description provided.",
        "license": "Apache-2.0",
        "type": "ai_generated",
        "created_by": created_by,
        "kind": kind,
        "capability_group": capability_group or "knowledge",
    }
    if description_cn:
        frontmatter["description_cn"] = description_cn
    if tool_refs:
        frontmatter["allowed-tools"] = tool_refs
    body = _build_sections(description, usage_guidelines)
    front_lines = [f"{key}: {_frontmatter_value(value)}" for key, value in frontmatter.items()]
    return f"---\n{chr(10).join(front_lines)}\n---\n\n{body}\n"


def _default_skill_json(
    skill_name,
    description,
    kind,
    tags=None,
    triggers=None,
    anti_triggers=None,
    references=None,
    tool_refs=None,
    workflow=None,
    creation_hints=None,
    capability_group=None,
    experience_policy=None,
    disclosure_level_defaults=None,
    python_dependencies=None,
    node_dependencies=None,
    script_refs=None,
    script_entries=None,
    asset_refs=None,
    source_format=None,
    tools=None,
):
    return {
        "version": 2,
        "name": skill_name,
        "kind": kind,
        "capability_group": capability_group or "knowledge",
        "description": description or "No description provided.",
        "tags": tags or [],
        "triggers": triggers or [],
        "anti_triggers": anti_triggers or [],
        "references": references or [],
        "tool_refs": tool_refs or [],
        "experience_policy": experience_policy or {"entry_storage": "experience/entries.jsonl", "summary_sync": "frontmatter_experience"},
        "disclosure_level_defaults": disclosure_level_defaults or {"default_prompt_level": "brief", "include_references": False, "include_experience_entries": False},
        "workflow": workflow or [],
        "creation_hints": creation_hints or {},
        "python_dependencies": python_dependencies or [],
        "node_dependencies": node_dependencies or [],
        "script_refs": script_refs or [],
        "script_entries": script_entries or [],
        "asset_refs": asset_refs or [],
        "source_format": source_format or "cowork",
        "tools": tools or [],
    }


def _extract_frontmatter(content):
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", content, re.DOTALL)
    if not match:
        return None, None
    return match.group(1), match.group(2)


def _parse_frontmatter_map(frontmatter_raw):
    values = {}
    for line in frontmatter_raw.split("\n"):
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        values[key.strip()] = val.strip()
    return values


def _join_frontmatter(values, preferred_order=None):
    ordered_keys = []
    if preferred_order:
        for key in preferred_order:
            if key in values:
                ordered_keys.append(key)
    for key in values.keys():
        if key not in ordered_keys:
            ordered_keys.append(key)
    return "\n".join([f"{k}: {values[k]}" for k in ordered_keys])


def create_new_skill(
    workspace_dir,
    skill_name,
    description,
    tools_list=None,
    tool_code=None,
    usage_guidelines="",
    description_cn=None,
    skill_kind="knowledge",
    tags=None,
    triggers=None,
    anti_triggers=None,
    references=None,
    tool_refs=None,
    workflow=None,
    creation_hints=None,
    capability_group=None,
    experience_policy=None,
    disclosure_level_defaults=None,
    python_dependencies=None,
    node_dependencies=None,
    script_refs=None,
    script_entries=None,
    asset_refs=None,
    instructions_md=None,
    resources=None,
    _context=None,
):
    staging_dir = ""
    backup_dir = ""
    published = False
    _SKILL_MUTATION_LOCK.acquire()
    try:
        log.info("skill_create_submit session=%s skill=%s", (_context or {}).get("session_id", ""), skill_name)
        check_cancel(_context)
        target_dir, error = _resolve_skill_dir(skill_name, target_scope="ai_only")
        if error:
            return error
        if os.path.exists(target_dir):
            raise ValueError("Skill already exists; use update_skill to preserve its existing content.")
        staging_dir = tempfile.mkdtemp(
            prefix=f".{skill_name}-staging-",
            dir=os.path.dirname(target_dir),
        )
        write_dir = staging_dir

        parsed_tools = _normalize_tools_list(tools_list)
        if tool_code and not parsed_tools:
            raise ValueError("tools_list with declarative schemas and bindings is required when tool_code is provided.")
        kind = (skill_kind or "knowledge").lower()
        if kind not in {"knowledge", "system"}:
            kind = "knowledge"

        referenced_tools = _normalize_json_value(tool_refs)
        if not isinstance(referenced_tools, list):
            referenced_tools = []
        if not referenced_tools:
            referenced_tools = [t.get("name") for t in parsed_tools if isinstance(t, dict) and t.get("name")]

        skill_json = _default_skill_json(
            skill_name,
            description,
            kind,
            tags=_normalize_json_value(tags) or [],
            triggers=_normalize_json_value(triggers) or [],
            anti_triggers=_normalize_json_value(anti_triggers) or [],
            references=_normalize_json_value(references) or [],
            tool_refs=referenced_tools,
            workflow=_normalize_json_value(workflow) or [],
            creation_hints=_normalize_json_value(creation_hints) or {},
            capability_group=capability_group,
            experience_policy=_normalize_json_value(experience_policy),
            disclosure_level_defaults=_normalize_json_value(disclosure_level_defaults),
            python_dependencies=_normalize_json_value(python_dependencies) or [],
            node_dependencies=_normalize_json_value(node_dependencies) or [],
            script_refs=_normalize_json_value(script_refs) or [],
            script_entries=_normalize_json_value(script_entries) or [],
            asset_refs=_normalize_json_value(asset_refs) or [],
            source_format="cowork",
            tools=parsed_tools,
        )

        md_content = _build_skill_md(
            skill_name,
            description,
            referenced_tools,
            instructions_md if instructions_md is not None else usage_guidelines,
            description_cn=description_cn,
            kind=kind,
            capability_group=capability_group,
        )
        with open(os.path.join(write_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(md_content)
        index_resources(skill_json, write_resources(write_dir, _normalize_json_value(resources), workspace_dir=workspace_dir))
        with open(os.path.join(write_dir, "skill.json"), "w", encoding="utf-8") as f:
            json.dump(skill_json, f, ensure_ascii=False, indent=2)
        if tool_code is not None:
            with open(os.path.join(write_dir, "impl.py"), "w", encoding="utf-8") as f:
                f.write(tool_code)
        _validate_staged_skill(staging_dir, skill_name, _context)
        check_cancel(_context)
        backup_dir = _swap_staged_skill(staging_dir, target_dir)
        staging_dir = ""
        published = True
        _publish_skill_change(_context, "created", skill_name)
        if backup_dir:
            shutil.rmtree(backup_dir)
            backup_dir = ""
        log.info("skill_create_completed skill=%s", skill_name)
        return f"Success: Created skill '{skill_name}' at '{target_dir}' as kind '{kind}'."
    except Exception as e:
        log.warning("skill_create_failed skill=%s error=%s", skill_name, type(e).__name__)
        if published:
            _rollback_published_skill(target_dir, backup_dir)
            backup_dir = ""
        return f"Error: {str(e)}"
    finally:
        if staging_dir:
            shutil.rmtree(staging_dir, ignore_errors=True)
        if backup_dir:
            shutil.rmtree(backup_dir, ignore_errors=True)
        _SKILL_MUTATION_LOCK.release()


def update_skill(
    workspace_dir,
    skill_name,
    target_scope="auto",
    description=None,
    tools_list=None,
    tool_code=None,
    usage_guidelines=None,
    description_cn=None,
    skill_kind=None,
    tags=None,
    triggers=None,
    anti_triggers=None,
    references=None,
    tool_refs=None,
    workflow=None,
    creation_hints=None,
    capability_group=None,
    experience_policy=None,
    disclosure_level_defaults=None,
    python_dependencies=None,
    node_dependencies=None,
    script_refs=None,
    script_entries=None,
    asset_refs=None,
    instructions_md=None,
    instruction_edits=None,
    resources=None,
    _context=None,
):
    staging_dir = ""
    backup_dir = ""
    published = False
    _SKILL_MUTATION_LOCK.acquire()
    try:
        log.info("skill_update_submit session=%s skill=%s", (_context or {}).get("session_id", ""), skill_name)
        check_cancel(_context)
        target_dir, error = _resolve_skill_dir(skill_name, target_scope=target_scope)
        if error:
            return error
        if not os.path.isdir(target_dir):
            return f"Error: Skill '{skill_name}' not found in scope '{target_scope}'."

        staging_dir = tempfile.mkdtemp(prefix=f".{skill_name}-staging-", dir=os.path.dirname(target_dir))
        shutil.copytree(target_dir, staging_dir, dirs_exist_ok=True)
        md_path = os.path.join(staging_dir, "SKILL.md")
        skill_json_path = os.path.join(staging_dir, "skill.json")
        impl_path = os.path.join(staging_dir, "impl.py")
        if not os.path.exists(md_path):
            return f"Error: SKILL.md not found for '{skill_name}'."

        with open(md_path, "r", encoding="utf-8") as f:
            md_content = f.read()
        frontmatter_raw, body = _extract_frontmatter(md_content)
        if frontmatter_raw is None:
            return "Error: Invalid SKILL.md format (missing frontmatter)."

        frontmatter_map = {}
        changed = []
        if description is not None:
            frontmatter_map["description"] = description
            changed.append("description")
        if description_cn is not None:
            frontmatter_map["description_cn"] = description_cn
            changed.append("description_cn")
        if skill_kind is not None:
            frontmatter_map["kind"] = skill_kind
            changed.append("kind")
        if capability_group is not None:
            frontmatter_map["capability_group"] = capability_group
            changed.append("capability_group")

        if tools_list is not None or tool_refs is not None:
            parsed_tools = _normalize_tools_list(tools_list)
            refs = _normalize_json_value(tool_refs)
            if not isinstance(refs, list):
                refs = []
            if not refs:
                refs = [t.get("name") for t in parsed_tools if isinstance(t, dict) and t.get("name")]
            frontmatter_map["allowed-tools"] = refs
            changed.append("allowed-tools")

        if instructions_md is not None and usage_guidelines is not None:
            raise ValueError("Use instructions_md or usage_guidelines, not both.")
        replacement = instructions_md if instructions_md is not None else usage_guidelines
        if replacement is not None:
            if instruction_edits:
                raise ValueError("Use full instructions or instruction_edits, not both.")
            body = str(replacement).strip() + "\n"
            changed.append("instructions_md")
        edits = _normalize_json_value(instruction_edits) or []
        if not isinstance(edits, list):
            raise ValueError("instruction_edits must be an array.")
        for edit in edits:
            if not isinstance(edit, dict) or not isinstance(edit.get("old_text"), str) or not edit["old_text"] or not isinstance(edit.get("new_text"), str):
                raise ValueError("Each instruction edit needs non-empty old_text and new_text.")
            if body.count(edit["old_text"]) != 1:
                raise ValueError("Instruction edit must match exactly once; reread the current skill.")
            body = body.replace(edit["old_text"], edit["new_text"], 1)
            changed.append("instruction_edit")

        preferred_order = ["name", "description", "description_cn", "license", "type", "created_by", "kind", "allowed-tools"]
        rebuilt_frontmatter = _patch_frontmatter(frontmatter_raw, frontmatter_map)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"---\n{rebuilt_frontmatter}\n---\n\n{body}")

        skill_json = {}
        if os.path.exists(skill_json_path):
            with open(skill_json_path, "r", encoding="utf-8") as f:
                skill_json = json.load(f)
        if description is not None:
            skill_json["description"] = description
        if skill_kind is not None:
            skill_json["kind"] = skill_kind
        for key, value in {
            "tags": tags,
            "triggers": triggers,
            "anti_triggers": anti_triggers,
            "references": references,
            "tool_refs": tool_refs,
            "workflow": workflow,
            "creation_hints": creation_hints,
            "capability_group": capability_group,
            "experience_policy": experience_policy,
            "disclosure_level_defaults": disclosure_level_defaults,
            "python_dependencies": python_dependencies,
            "node_dependencies": node_dependencies,
            "script_refs": script_refs,
            "script_entries": script_entries,
            "asset_refs": asset_refs,
        }.items():
            if value is not None:
                skill_json[key] = _normalize_json_value(value)
                changed.append(key)
        if tools_list is not None:
            parsed_tools = _normalize_tools_list(tools_list)
            skill_json["tools"] = parsed_tools
            if not skill_json.get("tool_refs"):
                skill_json["tool_refs"] = [t.get("name") for t in parsed_tools if isinstance(t, dict) and t.get("name")]
        original_resources = {}
        index_resources(skill_json, write_resources(
            staging_dir, _normalize_json_value(resources), workspace_dir=workspace_dir,
            original_resources=original_resources,
        ))
        if skill_json:
            skill_json.setdefault("version", 2)
            skill_json.setdefault("name", skill_name)
            with open(skill_json_path, "w", encoding="utf-8") as f:
                json.dump(skill_json, f, ensure_ascii=False, indent=2)

        if tool_code is not None:
            with open(impl_path, "w", encoding="utf-8") as f:
                f.write(tool_code)
            changed.append("impl.py")

        changed_desc = ", ".join(changed) if changed else "no fields"
        _validate_staged_skill(staging_dir, skill_name, _context)
        check_cancel(_context)
        for path, original in original_resources.items():
            current = package_path(target_dir, path)
            if (current.read_bytes() if current.exists() else None) != original:
                raise ValueError(f"Resource changed during update: {path}. Reopen the skill and try again.")
        backup_dir = _swap_staged_skill(staging_dir, target_dir)
        staging_dir = ""
        published = True
        _publish_skill_change(_context, "updated", skill_name)
        if backup_dir:
            shutil.rmtree(backup_dir)
            backup_dir = ""
        log.info("skill_update_completed skill=%s", skill_name)
        return f"Success: Updated skill '{skill_name}' at '{target_dir}'. Changed: {changed_desc}."
    except Exception as e:
        log.warning("skill_update_failed skill=%s error=%s", skill_name, type(e).__name__)
        if published:
            _rollback_published_skill(target_dir, backup_dir)
            backup_dir = ""
        return f"Error: {str(e)}"
    finally:
        if staging_dir:
            shutil.rmtree(staging_dir, ignore_errors=True)
        if backup_dir:
            shutil.rmtree(backup_dir, ignore_errors=True)
        _SKILL_MUTATION_LOCK.release()


def _read_agent_skill_name(source_dir):
    meta, _body = parse_skill_md_content(os.path.join(source_dir, "SKILL.md"))
    name = meta.get("name") if isinstance(meta.get("name"), str) else ""
    return name.strip()


def install_agent_skill(source_path, skill_name=None, skill_paths=None, ref="", source_type="auto",
                        expected_sha256="", workspace_dir=None, _context=None):
    """Install a local package or explicit HTTPS source through the normal execution authorization."""
    context = _context or {}
    source = str(source_path or "").strip()
    if workspace_dir and not source.startswith("https://") and not os.path.isabs(source):
        source = os.path.join(workspace_dir, source)
    staging_dirs, published_targets, prior_enabled = [], [], {}
    config = context.get("config_manager")
    phase = "source"
    log.info("skill_install_submit session=%s", context.get("session_id", ""))
    locked = False
    try:
        with prepare_source(source, skill_paths=_normalize_json_value(skill_paths), ref=ref,
                            source_type=source_type, expected_sha256=expected_sha256, context=context) as entries:
            if skill_name and len(entries) != 1:
                raise ValueError("skill_name can only be used for a single skill.")
            _SKILL_MUTATION_LOCK.acquire()
            locked = True
            phase = "validation"
            targets, names = [], set()
            install_root = os.path.join(get_app_data_dir(), "ai_skills")
            os.makedirs(install_root, exist_ok=True)
            for source_dir, provenance in entries:
                staging = tempfile.mkdtemp(prefix=".skill-install-", dir=install_root)
                os.rmdir(staging)
                staging_dirs.append(staging)
                inspected_digest = expected_sha256 if provenance.get("commit") or os.path.isdir(source) else ""
                stage_skill_package(source_dir, staging, expected_sha256=inspected_digest)
                if inspected_digest:
                    provenance["content_sha256"] = inspected_digest
                name = skill_name or _read_agent_skill_name(staging)
                target, error = _resolve_skill_dir(name, target_scope="ai_only")
                if error:
                    raise ValueError(error)
                if name.casefold() in names or os.path.exists(target):
                    raise ValueError(f"Skill already exists or is duplicated: {name}; use the update workflow.")
                names.add(name.casefold())
                adapt_staged_skill_directory(staging, skill_name=name, source_path=source_dir)
                metadata_path = os.path.join(staging, "skill.json")
                with open(metadata_path, encoding="utf-8") as handle:
                    metadata = json.load(handle)
                metadata["install_source"] = provenance
                with open(metadata_path, "w", encoding="utf-8") as handle:
                    json.dump(metadata, handle, ensure_ascii=False, indent=2)
                _validate_staged_skill(staging, name)
                targets.append((name, staging, target, metadata))
            check_cancel(context)
            publisher = context.get("skill_change_publisher")
            if not callable(publisher):
                raise RuntimeError("Skill runtime change publisher is unavailable.")
            phase = "save"
            for name, staging, target, _metadata in targets:
                # Destination cannot be overwritten by a concurrent creation.
                os.rename(staging, target)
                staging_dirs.remove(staging)
                published_targets.append(target)
                if config and hasattr(config, "set_skill_enabled"):
                    prior_enabled[name] = config.is_skill_enabled(name, True)
                    config.set_skill_enabled(name, True)
            phase = "publish"
            publisher({"action": "created", "skill_names": [item[0] for item in targets],
                       "source": "install_agent_skill", "session_id": context.get("session_id", "")})
            required = [f"{name}: {field.get('name')}" for name, _staging, _target, metadata in targets
                        for field in metadata.get("config_fields", [])
                        if isinstance(field, dict) and field.get("required") and not field.get("default")]
            log.info("skill_install_completed session=%s count=%s", context.get("session_id", ""), len(targets))
            result = "Success: Installed " + ", ".join(item[0] for item in targets) + "."
            if required:
                result += " Required configuration (verify in capability settings): " + "; ".join(required)
            return result
    except Exception as exc:
        with _SKILL_MUTATION_LOCK:
            for target in reversed(published_targets):
                shutil.rmtree(target)
            for name, enabled in prior_enabled.items():
                config.set_skill_enabled(name, enabled)
        log.warning("skill_install_failed session=%s phase=%s error=%s", context.get("session_id", ""), phase, type(exc).__name__)
        return f"Error installing skill ({phase}): {exc}"
    finally:
        if locked:
            _SKILL_MUTATION_LOCK.release()
        for staging in staging_dirs:
            shutil.rmtree(staging, ignore_errors=True)


def _resource_schema():
    return {"type": "array", "items": {"type": "object", "properties": {
        "kind": {"type": "string", "enum": ["reference", "script", "asset"]},
        "path": {"type": "string", "description": "Relative path under references/, scripts/ or assets/."},
        "content": {"type": "string", "description": "Text for reference/script resources."},
        "source_path": {"type": "string", "description": "Asset only: existing file in the current workspace."},
        "sha256": {"type": "string", "description": "Optional expected digest supplied by the user for an asset."},
        "previous_sha256": {"type": "string", "description": "Optional known digest of the existing resource when replacing it."},
        "description": {"type": "string"},
    }, "required": ["kind", "path"]}}


def _authoring_export(handler):
    properties, required = {}, []
    arrays = {"tags", "triggers", "anti_triggers", "references", "tool_refs", "workflow",
              "python_dependencies", "node_dependencies", "script_refs", "asset_refs", "skill_paths"}
    objects = {"creation_hints", "experience_policy", "disclosure_level_defaults"}
    for name, parameter in inspect.signature(handler).parameters.items():
        if name in {"workspace_dir", "_context"}:
            continue
        properties[name] = ({"type": "array", "items": {"type": "string"}} if name in arrays else
                            {"type": "object"} if name in objects else {"type": "string"})
        if name in {"tools_list", "script_entries"}:
            properties[name] = {"type": "array", "items": {"type": "object"}}
        if parameter.default is inspect.Parameter.empty:
            required.append(name)
    if "resources" in properties:
        properties["resources"] = _resource_schema()
        properties["instructions_md"]["description"] = "Complete Markdown body without frontmatter. Updates replace the whole body; for a small change use instruction_edits."
    if "instruction_edits" in properties:
        properties["instruction_edits"] = {"type": "array", "items": {"type": "object", "properties": {
            "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["old_text", "new_text"]}}
    if "source_type" in properties:
        properties["source_type"]["enum"] = ["auto", "repository", "package"]
        properties["source_path"]["description"] = "Local skill directory/ZIP/Markdown file, direct HTTPS package, or HTTPS Git repository. Read installation pages first to identify the actual source."
        properties["skill_paths"]["description"] = "Skill subdirectories inside a repository; omit for a single package with root SKILL.md."
        properties["ref"]["description"] = "Optional Git branch, tag or commit. Omit for the repository default HEAD."
        properties["expected_sha256"]["description"] = "Optional expected package or directory digest supplied by the user or a prior inspection."
    descriptions = {
        "create_new_skill": "Create a new skill with concise complete guidance and optional package resources. Existing names are not overwritten.",
        "update_skill": "Update an existing skill. Preserve unrelated fields; use instruction_edits for exact local edits and instructions_md only for intentional whole-body replacement.",
        "install_agent_skill": "Install local or explicit remote Agent Skill packages using the normal execution authorization. Downloads once, validates and installs the same content. No separate plan or second confirmation. Existing skills are not overwritten.",
    }
    return {"name": handler.__name__, "handler": handler, "description": descriptions[handler.__name__],
            "parameters": {"type": "object", "properties": properties, "required": required},
            "read_only": False, "destructive": True, "allowed_modes": ["execution"]}


def review_draft(workspace_dir, kind, draft_id="", title="", file_path="", content=None, _context=None):
    """Submit a persistent draft to the current conversation's review workspace."""
    from core.workbench import workbench_bridge
    return workbench_bridge().call("review_draft", {
        "kind": kind, "draft_id": draft_id, "title": title,
        "file_path": file_path, "content": content,
    }, _context)


TOOL_EXPORTS = [
    *[_authoring_export(handler) for handler in (create_new_skill, update_skill, install_agent_skill)],
    {
        "name": "review_draft",
        "handler": review_draft,
        "description": "Submit an Office file or reusable Skill draft to this conversation's workspace for human review. Does not publish or install it. Returns immediately; the user can review, edit and save later. Preserve an existing draft_id from the user's request. Never overwrite a draft already under review; submit a new draft for revisions.",
        "search_hint": "review draft office Word PowerPoint Excel skill 草稿 审阅 生成办公稿 沉淀技能",
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["office", "skill"]},
                "draft_id": {"type": "string", "description": "Existing review draft ID supplied by the user request. Omit to create a new draft."},
                "title": {"type": "string", "description": "Human-readable result name."},
                "file_path": {"type": "string", "description": "Office only: path to an existing, validated DOCX/PPTX/XLSX file in the current workspace. Generate the file with normal tools first, preferably under .cowork/drafts/."},
                "content": {
                    "type": "object", "description": "Skill only: guidance and optional text resources or template assets. No executable tool bindings or dependency installation.",
                    "properties": {
                        "skill_name": {"type": "string"},
                        "description": {"type": "string", "description": "When to use the skill."},
                        "instructions_md": {"type": "string", "description": "Complete guidance: scope, steps, verification, boundaries and source limitations."},
                        "resources": _resource_schema()
                    }, "required": ["skill_name", "description", "instructions_md"]
                }
            }, "required": ["kind"]
        },
        "read_only": False, "destructive": False,
        "allowed_modes": ["execution"], "result_format": "json"
    },
]
