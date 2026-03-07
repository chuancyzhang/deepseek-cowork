import json
import os
import re
import shutil
from core.env_utils import get_app_data_dir


def _is_valid_skill_name(skill_name):
    return bool(skill_name) and all(c.isalnum() or c == "-" for c in skill_name)


def _normalize_tools_list(tools_list):
    if isinstance(tools_list, str):
        tools_list = json.loads(tools_list)
    if not isinstance(tools_list, list):
        raise ValueError("tools_list must be a list.")
    return tools_list


def _build_skill_md(skill_name, description, tools_list, usage_guidelines, description_cn=None, created_by="ai", skill_type="ai_generated"):
    tool_names = [t.get("name") for t in tools_list if isinstance(t, dict) and t.get("name")]
    allowed_tools_str = ", ".join(tool_names)
    desc_cn_line = f"description_cn: {description_cn}\n" if description_cn else ""
    md_content = f"""---
name: {skill_name}
description: {description}
{desc_cn_line}license: Apache-2.0
type: {skill_type}
created_by: {created_by}
allowed-tools: [{allowed_tools_str}]
---

# {skill_name.capitalize()} Skill

{description}

{usage_guidelines}

## Tools

"""
    for tool in tools_list:
        if not isinstance(tool, dict):
            continue
        t_name = tool.get("name", "unknown")
        t_desc = tool.get("description", "No description.")
        md_content += f"### {t_name}\n{t_desc}\n\n"
    return md_content


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


def create_new_skill(workspace_dir, skill_name, description, tools_list, tool_code, usage_guidelines, description_cn=None):
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
        md_content = _build_skill_md(skill_name, description, parsed_tools, usage_guidelines, description_cn=description_cn)
        with open(os.path.join(target_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(md_content)
        with open(os.path.join(target_dir, "impl.py"), "w", encoding="utf-8") as f:
            f.write(tool_code)
        return f"Success: {action} skill '{skill_name}' at '{target_dir}' with {len(parsed_tools)} tools."
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
    description_cn=None
):
    try:
        target_dir, error = _resolve_skill_dir(skill_name, target_scope=target_scope)
        if error:
            return error
        if not os.path.isdir(target_dir):
            return f"Error: Skill '{skill_name}' not found in scope '{target_scope}'."

        md_path = os.path.join(target_dir, "SKILL.md")
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
        if tools_list is not None:
            parsed_tools = _normalize_tools_list(tools_list)
            tool_names = [t.get("name") for t in parsed_tools if isinstance(t, dict) and t.get("name")]
            frontmatter_map["allowed-tools"] = "[" + ", ".join(tool_names) + "]"
            changed.append("allowed-tools")
            if description is None and body:
                body = body.split("\n## Tools\n", 1)[0].rstrip() + "\n\n## Tools\n\n"
                for tool in parsed_tools:
                    t_name = tool.get("name", "unknown")
                    t_desc = tool.get("description", "No description.")
                    body += f"### {t_name}\n{t_desc}\n\n"
                changed.append("tools-section")

        if usage_guidelines is not None:
            if "\n## Tools\n" in body:
                _, tools_section = body.split("\n## Tools\n", 1)
                body = usage_guidelines.rstrip() + "\n\n## Tools\n" + tools_section
            else:
                body = usage_guidelines
            changed.append("usage_guidelines")

        preferred_order = ["name", "description", "description_cn", "license", "type", "created_by", "allowed-tools"]
        rebuilt_frontmatter = _join_frontmatter(frontmatter_map, preferred_order=preferred_order)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"---\n{rebuilt_frontmatter}\n---\n{body}")

        if tool_code is not None:
            with open(impl_path, "w", encoding="utf-8") as f:
                f.write(tool_code)
            changed.append("impl.py")

        changed_desc = ", ".join(changed) if changed else "no fields"
        return f"Success: Updated skill '{skill_name}' at '{target_dir}'. Changed: {changed_desc}."
    except Exception as e:
        return f"Error: {str(e)}"


def convert_claude_skill(source_path, skill_name=None):
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

        shutil.copytree(source_path, target_dir)
        scripts_dir = os.path.join(target_dir, "scripts")
        generated_tools = []
        impl_code_lines = [
            "import subprocess",
            "import sys",
            "import os",
            "import shlex",
            "",
            "def _run_script(script_name, args_str):",
            "    base_dir = os.path.dirname(__file__)",
            "    script_path = os.path.join(base_dir, 'scripts', script_name)",
            "    cmd = []",
            "    if script_name.endswith('.py'):",
            "        cmd = [sys.executable, script_path]",
            "    elif script_name.endswith('.sh'):",
            "        cmd = ['bash', script_path]",
            "    elif script_name.endswith('.js'):",
            "        cmd = ['node', script_path]",
            "    else:",
            "        cmd = [script_path]",
            "    if args_str:",
            "        cmd.extend(shlex.split(args_str))",
            "    try:",
            "        result = subprocess.run(cmd, capture_output=True, text=True, cwd=base_dir)",
            "        output = result.stdout",
            "        if result.stderr:",
            "            output += '\\n[STDERR]\\n' + result.stderr",
            "        return output",
            "    except Exception as e:",
            "        return f'Execution failed: {str(e)}'",
            ""
        ]
        if os.path.exists(scripts_dir):
            for file in os.listdir(scripts_dir):
                if file.startswith(".") or file.startswith("__"):
                    continue
                file_path = os.path.join(scripts_dir, file)
                if not os.path.isfile(file_path):
                    continue
                base_name = os.path.splitext(file)[0]
                tool_name = f"run_{base_name.replace('-', '_')}"
                impl_code_lines.append(f"def {tool_name}(args=''):")
                impl_code_lines.append("    \"\"\"Executes a converted script.\"\"\"")
                impl_code_lines.append(f"    return _run_script('{file}', args)")
                impl_code_lines.append("")
                generated_tools.append(tool_name)

        with open(os.path.join(target_dir, "impl.py"), "w", encoding="utf-8") as f:
            f.write("\n".join(impl_code_lines))

        md_path = os.path.join(target_dir, "SKILL.md")
        if os.path.exists(md_path):
            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read()
            frontmatter_raw, body = _extract_frontmatter(content)
            tools_str = ", ".join(generated_tools)
            if frontmatter_raw is not None:
                frontmatter_map = _parse_frontmatter_map(frontmatter_raw)
                frontmatter_map["allowed-tools"] = "[" + tools_str + "]"
                if "type" not in frontmatter_map:
                    frontmatter_map["type"] = "ai_generated"
                if "created_by" not in frontmatter_map:
                    frontmatter_map["created_by"] = "ai"
                body += "\n\n## Cowork Integration\nThis skill has been adapted from a Claude Skill.\n"
                for t in generated_tools:
                    body += f"- `{t}(args)`\n"
                rebuilt_frontmatter = _join_frontmatter(frontmatter_map, preferred_order=["name", "description", "description_cn", "license", "type", "created_by", "allowed-tools"])
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(f"---\n{rebuilt_frontmatter}\n---\n{body}")
            else:
                header = f"---\nname: {skill_name}\ndescription: Auto-converted Claude Skill.\ntype: ai_generated\ncreated_by: ai\nallowed-tools: [{tools_str}]\n---\n\n"
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(header + content)

        return f"Success: Converted '{source_path}' to '{target_dir}'. Generated {len(generated_tools)} wrapper tools."
    except Exception as e:
        return f"Error converting skill: {str(e)}"
