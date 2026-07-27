import json
import os
import re
import shutil
import tempfile
import ast
import threading

from core.env_utils import get_app_data_dir
from core.remote_skill_installer import run_remote_skill_installer_agent
from core.skill_adapter import adapt_skill_directory, parse_skill_md_content, resolve_agent_skill_source


_SKILL_MUTATION_LOCK = threading.RLock()


def _is_valid_skill_name(skill_name):
    return bool(skill_name) and all(c.isalnum() or c == "-" for c in skill_name)


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
    if not _is_valid_skill_name(skill_name):
        return None, "Error: Skill name must be alphanumeric (hyphens allowed)."
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


def _validate_staged_skill(skill_dir, skill_name):
    md_path = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(md_path):
        raise ValueError("SKILL.md is required.")
    meta, _body = parse_skill_md_content(md_path)
    declared_name = str((meta or {}).get("name") or "").strip()
    if not declared_name:
        raise ValueError("SKILL.md frontmatter name is required.")
    if declared_name != skill_name:
        raise ValueError(f"SKILL.md name '{declared_name}' does not match '{skill_name}'.")
    if not str((meta or {}).get("description") or "").strip():
        raise ValueError("SKILL.md frontmatter description is required.")
    json_path = os.path.join(skill_dir, "skill.json")
    if os.path.isfile(json_path):
        with open(json_path, "r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("skill.json must contain a JSON object.")
    impl_path = os.path.join(skill_dir, "impl.py")
    if os.path.isfile(impl_path):
        with open(impl_path, "r", encoding="utf-8-sig") as handle:
            ast.parse(handle.read(), filename=impl_path)


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
    sections = []
    sections.append("# Skill Purpose\n" + (description or "Describe the purpose of this skill."))
    sections.append("## When to Use\n" + (usage_guidelines or "Describe when this skill should be used."))
    sections.append("## When Not to Use\nDescribe cases where this skill is not a good fit.")
    sections.append("## Common Pitfalls\nRecord common mistakes, hidden assumptions, and troubleshooting notes.")
    sections.append("## Experience / Lessons Learned\nAdd reusable lessons here as the skill evolves.")
    sections.append("## Recommended Workflow\nDocument the recommended sequence of steps and decision points.")
    sections.append("## Recommended Tools\nList the lightweight tools that usually work best for this skill.")
    sections.append("## Interface Details\n" + (interface_details or "Document interfaces, relevant tools, parameters, outputs, and caveats."))
    sections.append("## Constraints and Safety Rules\nDocument important safety boundaries and operational constraints.")
    sections.append("## References\nLink to supporting references or leave notes about what should live in references/.")
    return "\n\n".join(sections)


def _frontmatter_value(value):
    if isinstance(value, list):
        return "[" + ", ".join([json.dumps(item, ensure_ascii=False) for item in value]) + "]"
    return str(value)


def _build_skill_md(skill_name, description, tool_refs, usage_guidelines, description_cn=None, kind="knowledge", created_by="ai", capability_group=None):
    frontmatter = {
        "name": skill_name,
        "description": description or "No description provided.",
        "license": "Apache-2.0",
        "type": "ai_generated",
        "created_by": created_by,
        "kind": kind,
        "capability_group": capability_group or "knowledge",
        "experience": [],
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
    _context=None,
):
    staging_dir = ""
    backup_dir = ""
    published = False
    _SKILL_MUTATION_LOCK.acquire()
    try:
        target_dir, error = _resolve_skill_dir(skill_name, target_scope="ai_only")
        if error:
            return error
        action = "Updated" if os.path.exists(target_dir) else "Created"
        staging_dir = tempfile.mkdtemp(
            prefix=f".{skill_name}-staging-",
            dir=os.path.dirname(target_dir),
        )
        if action == "Updated":
            shutil.copytree(target_dir, staging_dir, dirs_exist_ok=True)
        write_dir = staging_dir

        parsed_tools = _normalize_tools_list(tools_list)
        if tool_code and not parsed_tools:
            raise ValueError("tools_list with declarative schemas and bindings is required when tool_code is provided.")
        if tool_code:
            ast.parse(str(tool_code), filename=f"{skill_name}/impl.py")
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
            usage_guidelines,
            description_cn=description_cn,
            kind=kind,
            capability_group=capability_group,
        )
        with open(os.path.join(write_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(md_content)
        with open(os.path.join(write_dir, "skill.json"), "w", encoding="utf-8") as f:
            json.dump(skill_json, f, ensure_ascii=False, indent=2)
        os.makedirs(os.path.join(write_dir, "experience"), exist_ok=True)
        if tool_code is not None:
            with open(os.path.join(write_dir, "impl.py"), "w", encoding="utf-8") as f:
                f.write(tool_code)
        _validate_staged_skill(staging_dir, skill_name)
        backup_dir = _swap_staged_skill(staging_dir, target_dir)
        staging_dir = ""
        published = True
        _publish_skill_change(_context, "created" if action == "Created" else "updated", skill_name)
        if backup_dir:
            shutil.rmtree(backup_dir)
            backup_dir = ""
        return f"Success: {action} skill '{skill_name}' at '{target_dir}' as kind '{kind}'."
    except Exception as e:
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
    _context=None,
):
    staging_dir = ""
    backup_dir = ""
    published = False
    _SKILL_MUTATION_LOCK.acquire()
    try:
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

        frontmatter_map = _parse_frontmatter_map(frontmatter_raw)
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
            frontmatter_map["allowed-tools"] = "[" + ", ".join(refs) + "]"
            changed.append("allowed-tools")

        if usage_guidelines is not None:
            body = _build_sections(description or frontmatter_map.get("description", ""), usage_guidelines)
            changed.append("usage_guidelines")

        preferred_order = ["name", "description", "description_cn", "license", "type", "created_by", "kind", "allowed-tools"]
        rebuilt_frontmatter = _join_frontmatter(frontmatter_map, preferred_order=preferred_order)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"---\n{rebuilt_frontmatter}\n---\n\n{body}")

        skill_json = {}
        if os.path.exists(skill_json_path):
            with open(skill_json_path, "r", encoding="utf-8") as f:
                try:
                    skill_json = json.load(f)
                except Exception:
                    skill_json = {}
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
        if tools_list is not None and skill_json.get("tool_refs") in (None, []):
            parsed_tools = _normalize_tools_list(tools_list)
            skill_json["tool_refs"] = [t.get("name") for t in parsed_tools if isinstance(t, dict) and t.get("name")]
        if skill_json:
            skill_json.setdefault("version", 2)
            skill_json.setdefault("name", skill_name)
            with open(skill_json_path, "w", encoding="utf-8") as f:
                json.dump(skill_json, f, ensure_ascii=False, indent=2)
        os.makedirs(os.path.join(target_dir, "experience"), exist_ok=True)

        if tool_code is not None:
            ast.parse(str(tool_code), filename=f"{skill_name}/impl.py")
            with open(impl_path, "w", encoding="utf-8") as f:
                f.write(tool_code)
            changed.append("impl.py")

        changed_desc = ", ".join(changed) if changed else "no fields"
        _validate_staged_skill(staging_dir, skill_name)
        backup_dir = _swap_staged_skill(staging_dir, target_dir)
        staging_dir = ""
        published = True
        _publish_skill_change(_context, "updated", skill_name)
        if backup_dir:
            shutil.rmtree(backup_dir)
            backup_dir = ""
        return f"Success: Updated skill '{skill_name}' at '{target_dir}'. Changed: {changed_desc}."
    except Exception as e:
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


def install_agent_skill(source_path, skill_name=None, _context=None):
    staging_dir = ""
    backup_dir = ""
    published = False
    _SKILL_MUTATION_LOCK.acquire()
    try:
        if not os.path.exists(source_path):
            return f"Error: Source path '{source_path}' does not exist."
        resolved_source, temp_dir = resolve_agent_skill_source(source_path)
        if not skill_name:
            skill_name = _read_agent_skill_name(resolved_source)
        if not skill_name:
            return "Error: Source SKILL.md must include a frontmatter 'name'."
        skill_name = skill_name.strip()
        if not _is_valid_skill_name(skill_name):
            return "Error: Skill name must be alphanumeric (hyphens allowed)."
        target_dir, error = _resolve_skill_dir(skill_name, target_scope="ai_only")
        if error:
            return error
        if os.path.exists(target_dir):
            return f"Error: Target skill directory '{target_dir}' already exists. Please delete it or choose a different name."
        staging_dir = tempfile.mkdtemp(prefix=f".{skill_name}-staging-", dir=os.path.dirname(target_dir))
        os.rmdir(staging_dir)
        result = adapt_skill_directory(resolved_source, staging_dir, skill_name=skill_name, source_format="agent_skill")
        skill_json_path = os.path.join(staging_dir, "skill.json")
        skill_json = {}
        if os.path.isfile(skill_json_path):
            with open(skill_json_path, "r", encoding="utf-8") as f:
                skill_json = json.load(f)
        _validate_staged_skill(staging_dir, skill_name)
        backup_dir = _swap_staged_skill(staging_dir, target_dir)
        staging_dir = ""
        published = True
        _publish_skill_change(_context, "created", skill_name)
        if backup_dir:
            shutil.rmtree(backup_dir)
            backup_dir = ""
        return f"Success: Installed agent skill '{result.get('skill_name')}' at '{target_dir}'."
    except Exception as e:
        if published:
            _rollback_published_skill(target_dir, backup_dir)
            backup_dir = ""
        return f"Error installing agent skill: {str(e)}"
    finally:
        if staging_dir:
            shutil.rmtree(staging_dir, ignore_errors=True)
        if backup_dir:
            shutil.rmtree(backup_dir, ignore_errors=True)
        if "temp_dir" in locals() and temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        _SKILL_MUTATION_LOCK.release()


def remote_skill_installer_agent(
    request="",
    continuation_id="",
    decision="",
    config_overrides=None,
    _context=None,
):
    """Inspect and install remote Agent Skills through a constrained specialist agent."""
    context = _context if isinstance(_context, dict) else {}
    return run_remote_skill_installer_agent(
        request=request,
        continuation_id=continuation_id,
        decision=decision,
        config_overrides=config_overrides,
        app_data_dir=get_app_data_dir(),
        context=context,
        runner=context.get("remote_skill_installer_agent_runner"),
        mutation_lock=_SKILL_MUTATION_LOCK,
    )


TOOL_EXPORTS = [
    {
        "name": "remote_skill_installer_agent",
        "handler": remote_skill_installer_agent,
        "description": (
            "Delegate remote Skill installation to a constrained specialist agent. "
            "Use when the user provides an HTTPS skill.md or remote Skill installation URL. "
            "The first call returns a fixed preview requiring user approval; after approval, "
            "call again with the returned continuation_id and decision='confirm'. Pass the "
            "original user request unchanged. Do not retry by rephrasing, browsing, adding a "
            "commit/path, or splitting Skills."
        ),
        "search_hint": (
            "read remote skill.md install skill URL API key token credential "
            "安装远程Skill 阅读skill.md 带Key能力"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "request": {
                    "type": "string",
                    "description": "Initial user request containing the remote HTTPS Skill entry URL.",
                },
                "continuation_id": {
                    "type": "string",
                    "description": "Opaque continuation ID returned by the inspection call.",
                },
                "decision": {
                    "type": "string",
                    "enum": ["", "confirm", "cancel"],
                    "description": "Leave empty for inspection; use confirm or cancel only after user approval.",
                },
                "config_overrides": {
                    "type": "object",
                    "description": "Optional complete config_fields replacement keyed by Skill name.",
                    "additionalProperties": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
                },
            },
            "required": [],
        },
        "destructive": True,
        "requires_user_interaction": True,
        "should_defer": True,
        "result_format": "json",
    },
]
