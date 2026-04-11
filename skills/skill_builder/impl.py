import json
import os
import re

from core.env_utils import get_app_data_dir
from core.sandbox_runtime import install_skill_dependencies
from core.skill_adapter import adapt_skill_directory, detect_external_skill_format


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
):
    try:
        target_dir, error = _resolve_skill_dir(skill_name, target_scope="ai_only")
        if error:
            return error
        action = "Created"
        if os.path.exists(target_dir):
            action = "Updated"
        else:
            os.makedirs(target_dir, exist_ok=True)

        parsed_tools = _normalize_tools_list(tools_list)
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
        with open(os.path.join(target_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(md_content)
        with open(os.path.join(target_dir, "skill.json"), "w", encoding="utf-8") as f:
            json.dump(skill_json, f, ensure_ascii=False, indent=2)
        os.makedirs(os.path.join(target_dir, "experience"), exist_ok=True)
        dependency_status = install_skill_dependencies(
            skill_name,
            skill_json.get("python_dependencies") or [],
            skill_json.get("node_dependencies") or [],
        )
        if tool_code is not None:
            with open(os.path.join(target_dir, "impl.py"), "w", encoding="utf-8") as f:
                f.write(tool_code)
        if not dependency_status.get("ok"):
            return f"Success: {action} skill '{skill_name}' at '{target_dir}', but dependency setup is incomplete: {dependency_status.get('message')}"
        return f"Success: {action} skill '{skill_name}' at '{target_dir}' as kind '{kind}'."
    except Exception as e:
        return f"Error: {str(e)}"


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
):
    try:
        target_dir, error = _resolve_skill_dir(skill_name, target_scope=target_scope)
        if error:
            return error
        if not os.path.isdir(target_dir):
            return f"Error: Skill '{skill_name}' not found in scope '{target_scope}'."

        md_path = os.path.join(target_dir, "SKILL.md")
        skill_json_path = os.path.join(target_dir, "skill.json")
        impl_path = os.path.join(target_dir, "impl.py")
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
            dependency_status = install_skill_dependencies(
                skill_name,
                skill_json.get("python_dependencies") or [],
                skill_json.get("node_dependencies") or [],
            )
            if not dependency_status.get("ok"):
                changed.append("dependencies_not_ready")
        os.makedirs(os.path.join(target_dir, "experience"), exist_ok=True)

        if tool_code is not None:
            with open(impl_path, "w", encoding="utf-8") as f:
                f.write(tool_code)
            changed.append("impl.py")

        changed_desc = ", ".join(changed) if changed else "no fields"
        return f"Success: Updated skill '{skill_name}' at '{target_dir}'. Changed: {changed_desc}."
    except Exception as e:
        return f"Error: {str(e)}"


def convert_claude_skill(source_path, skill_name=None):
    return convert_external_skill(source_path, skill_name=skill_name, source_format="claude")


def convert_openclaw_skill(source_path, skill_name=None):
    return convert_external_skill(source_path, skill_name=skill_name, source_format="openclaw")


def convert_external_skill(source_path, skill_name=None, source_format="auto"):
    try:
        if not os.path.exists(source_path):
            return f"Error: Source path '{source_path}' does not exist."
        source_name = os.path.basename(os.path.normpath(source_path))
        if not skill_name:
            skill_name = source_name
        if not _is_valid_skill_name(skill_name):
            return "Error: Skill name must be alphanumeric (hyphens allowed)."
        target_dir, error = _resolve_skill_dir(skill_name, target_scope="ai_only")
        if error:
            return error
        if os.path.exists(target_dir):
            return f"Error: Target skill directory '{target_dir}' already exists. Please delete it or choose a different name."
        result = adapt_skill_directory(source_path, target_dir, skill_name=skill_name, source_format=source_format)
        detected = result.get("source_format") or detect_external_skill_format(source_path)
        return f"Success: Adapted '{source_path}' to '{target_dir}' as Cowork skill format from source '{detected}'."
    except Exception as e:
        return f"Error converting skill: {str(e)}"
