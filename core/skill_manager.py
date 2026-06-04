import importlib.util
import inspect
import json
import os
import re
import shutil
import sys
import time
import uuid
import tempfile
import zipfile
import ast

from .env_utils import ensure_package_installed, get_app_data_dir
from .mcp_client import (
    build_mcp_skill_name,
    build_mcp_tool_name,
    call_mcp_tool,
    list_mcp_server_tools,
    mcp_package_available,
    mcp_transport_label,
    summarize_mcp_server,
)
from .sandbox_runtime import build_sandbox_env, install_skill_dependencies
from .skill_adapter import (
    EXCLUDED_DIRS,
    adapt_skill_directory,
    discover_importable_skill_dirs,
    discover_skill_artifacts,
)
from .tool_registry import ToolRegistry
from .clarify_mode import normalize_selected_skill_names


def _tokenize(text):
    return set(re.findall(r"[a-z0-9][a-z0-9_\-]+", str(text or "").casefold()))


def _normalize_search_text(text):
    lowered = str(text or "").casefold()
    return re.sub(r"[^a-z0-9]+", "", lowered)


def _json_copy(value, fallback):
    if value is None:
        return fallback
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except Exception:
        return fallback


def _humanize_skill_name(skill_name):
    text = (skill_name or "").replace("_", "-")
    mapping = {
        "agent-manager": "协作代理",
        "file-system": "文件整理与读写",
        "general-experience": "通用经验增强",
        "history-query": "历史记录查询",
        "interaction": "用户确认与交付",
        "memory-manager": "长期记忆",
        "meta-tools": "任务辅助工具",
        "command-tools": "命令与搜索工具",
        "financial-data-akshare": "AKShare 金融数据",
        "python-runner": "Python 执行",
        "skill-importer": "能力导入",
        "skill_builder": "能力创建",
        "system-tools": "环境与应用自动化",
        "web-search": "网页搜索",
    }
    return mapping.get(text, text.replace("-", " ").title())


class SkillManager:
    ALWAYS_ALLOWED_SCOPE_TOOLS = {"tool_search", "parallel_tools"}
    MCP_SOURCE_FORMAT = "mcp_server"

    GROUP_DEFAULTS = {
        "file-system": "file-information-interaction",
        "financial-data-akshare": "file-information-interaction",
        "web-search": "file-information-interaction",
        "command-tools": "code-command-execution",
        "system-tools": "code-command-execution",
        "python-runner": "code-command-execution",
        "interaction": "ai-human-interaction",
        "history-query": "memory-meta",
        "memory-manager": "memory-meta",
        "meta-tools": "memory-meta",
        "general-experience": "memory-meta",
        "agent-manager": "memory-meta",
        "skill_builder": "system-skill",
        "skill-importer": "system-skill",
    }

    SYSTEM_SKILLS = {"skill_builder", "skill-importer"}

    def __init__(self, workspace_dir=None, config_manager=None):
        self.workspace_dir = workspace_dir
        self.config_manager = config_manager
        self.skills_dirs = []

        data_dir = get_app_data_dir()
        self.skills_dirs.append(os.path.join(data_dir, "skills"))
        self.skills_dirs.append(os.path.join(data_dir, "ai_skills"))

        if getattr(sys, "frozen", False):
            if hasattr(sys, "_MEIPASS"):
                self.skills_dirs.append(os.path.join(sys._MEIPASS, "skills"))
            else:
                base_dir = os.path.dirname(sys.executable)
                internal_path = os.path.join(base_dir, "_internal", "skills")
                if os.path.exists(internal_path):
                    self.skills_dirs.append(internal_path)
                self.skills_dirs.append(os.path.join(base_dir, "skills"))
                self.skills_dirs.append(os.path.join(base_dir, "ai_skills"))
        else:
            repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.skills_dirs.append(os.path.join(repo_root, "skills"))
            self.skills_dirs.append(os.path.join(repo_root, "ai_skills"))

        self.tools = {}
        self.tool_definitions = []
        self.tool_registry = ToolRegistry()
        self.tool_to_skill_map = {}
        self.tool_records = {}
        self.skill_to_tools = {}
        self.loaded_skills_meta = {}
        self.loaded_skill_sources = {}
        self.skill_prompts_brief = []
        self.skill_prompts_full = {}
        self.skill_records = {}
        self.experience_packages = {}
        self.last_load_time = 0
        self.load_skills()

    def set_workspace_dir(self, workspace_dir):
        self.workspace_dir = workspace_dir

    def _scan_dist_dirs(self):
        if getattr(sys, "frozen", False):
            base_dir = os.path.dirname(sys.executable)
            for folder in ("skills", "ai_skills"):
                candidate = os.path.join(base_dir, folder)
                if os.path.isdir(candidate) and candidate not in self.skills_dirs:
                    self.skills_dirs.append(candidate)
            return

    def _parse_frontmatter_value(self, raw):
        raw = (raw or "").strip()
        if not raw:
            return ""
        if raw.startswith("[") and raw.endswith("]"):
            try:
                return json.loads(raw)
            except Exception:
                inner = raw[1:-1].strip()
                if not inner:
                    return []
                return [item.strip().strip("\"'") for item in inner.split(",")]
        if raw.startswith("{") and raw.endswith("}"):
            try:
                return json.loads(raw)
            except Exception:
                return raw
        if raw.lower() in {"true", "false"}:
            return raw.lower() == "true"
        if raw.isdigit():
            try:
                return int(raw)
            except Exception:
                pass
        return raw.strip("\"'")

    def _format_frontmatter_value(self, value):
        if isinstance(value, list):
            return "[" + ", ".join(json.dumps(item, ensure_ascii=False) for item in value) + "]"
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    def _parse_skill_md_content(self, md_path):
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read()
            match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)", content, re.DOTALL)
            if not match:
                return {}, content
            frontmatter_raw = match.group(1)
            body = (match.group(2) or "").strip()
            meta = {}
            for line in frontmatter_raw.split("\n"):
                line = line.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                key, val = line.split(":", 1)
                meta[key.strip()] = self._parse_frontmatter_value(val)
            return meta, body
        except Exception:
            return {}, ""

    def _load_skill_json(self, skill_path):
        skill_json_path = os.path.join(skill_path, "skill.json")
        if not os.path.exists(skill_json_path):
            return {}
        try:
            with open(skill_json_path, "r", encoding="utf-8-sig") as f:
                payload = json.load(f)
            return payload if isinstance(payload, dict) else {}
        except Exception as e:
            print(f"[SkillManager] Failed to parse skill.json at {skill_json_path}: {e}")
            return {}

    def _coerce_string_list(self, value):
        if isinstance(value, list):
            return [item.strip() for item in value if isinstance(item, str) and item.strip()]
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return [item.strip() for item in parsed if isinstance(item, str) and item.strip()]
            except Exception:
                pass
            return [item.strip().strip("\"'") for item in re.split(r"[\r\n,]+", text) if item.strip().strip("\"'")]
        return []

    def _coerce_bool(self, value, default=False):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "on"}:
                return True
            if lowered in {"false", "0", "no", "off"}:
                return False
        return default

    def _coerce_dict_list(self, value):
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return [item for item in parsed if isinstance(item, dict)]
            except Exception:
                return []
        return []

    def _normalize_skill_kind(self, meta, spec):
        kind = spec.get("kind") or meta.get("kind") or ""
        if not isinstance(kind, str):
            kind = ""
        kind = kind.strip().lower()
        if kind in {"knowledge", "system"}:
            return kind
        return "knowledge"

    def _normalize_script_entries(self, entries):
        normalized = []
        seen = set()
        for item in self._coerce_dict_list(entries):
            path = item.get("path")
            name = item.get("name")
            runtime = item.get("runtime")
            if not isinstance(path, str) or not path.strip():
                continue
            normalized_item = {
                "name": name.strip() if isinstance(name, str) and name.strip() else os.path.splitext(os.path.basename(path))[0],
                "path": os.path.normpath(path.strip()),
                "runtime": runtime.strip().lower() if isinstance(runtime, str) and runtime.strip() else "bash",
                "description": item.get("description") if isinstance(item.get("description"), str) else "",
                "args_schema": item.get("args_schema") if isinstance(item.get("args_schema"), dict) else {},
                "default_args": [str(arg) for arg in item.get("default_args", [])] if isinstance(item.get("default_args"), list) else [],
                "entrypoint_style": item.get("entrypoint_style") if isinstance(item.get("entrypoint_style"), str) and item.get("entrypoint_style").strip() else "script",
            }
            key = (normalized_item["path"].lower(), normalized_item["name"].lower())
            if key in seen:
                continue
            seen.add(key)
            normalized.append(normalized_item)
        return normalized

    def _infer_capability_group(self, skill_name, meta, spec):
        group = (
            spec.get("capability_group")
            or meta.get("capability_group")
            or self.GROUP_DEFAULTS.get(skill_name, "knowledge")
        )
        if isinstance(group, str) and group.strip():
            return group.strip()
        return self.GROUP_DEFAULTS.get(skill_name, "knowledge")

    def _normalize_disclosure_defaults(self, spec):
        defaults = spec.get("disclosure_level_defaults") if isinstance(spec, dict) else spec
        normalized = defaults if isinstance(defaults, dict) else {}
        prompt_level = normalized.get("default_prompt_level")
        if not isinstance(prompt_level, str) or not prompt_level.strip():
            if isinstance(defaults, str) and defaults.strip():
                prompt_level = defaults.strip()
            else:
                prompt_level = "brief"
        return {
            "default_prompt_level": prompt_level,
            "include_references": self._coerce_bool(normalized.get("include_references"), default=False),
            "include_experience_entries": self._coerce_bool(normalized.get("include_experience_entries"), default=False),
        }

    def _infer_execution_surface(self, spec):
        script_entries = spec.get("script_entries") or []
        tool_refs = spec.get("tool_refs") or []
        if script_entries:
            return "skill_script"
        if tool_refs:
            return "tool_refs"
        return "knowledge"

    def _infer_prompt_disclosure(self, spec):
        prompt_disclosure = spec.get("prompt_disclosure")
        source_format = str(spec.get("source_format") or "").strip().lower()
        should_upgrade = source_format in {"agent_skill", "claude", "openclaw"} or bool(spec.get("script_entries"))
        if isinstance(prompt_disclosure, str) and prompt_disclosure.strip():
            normalized = prompt_disclosure.strip()
            if normalized == "full_on_match" or not should_upgrade:
                return normalized
        return "full_on_match" if should_upgrade else "brief_only"

    def _infer_preferred_script_name(self, spec):
        script_entries = spec.get("script_entries") or []
        if len(script_entries) != 1:
            return ""
        name = script_entries[0].get("name")
        return str(name or "").strip()

    def _script_execution_hint(self, spec):
        if self._infer_execution_surface(spec) != "skill_script":
            return ""
        return (
            "When this skill is matched, call `run_skill_script` with the listed script entry "
            "instead of locating the skill directory or script path with `glob`, `grep`, or `bash`."
        )

    def _normalize_experience_policy(self, spec):
        policy = spec.get("experience_policy") if isinstance(spec, dict) else spec
        normalized = policy if isinstance(policy, dict) else {}
        entry_storage = normalized.get("entry_storage")
        summary_sync = normalized.get("summary_sync")
        if isinstance(policy, str) and policy.strip():
            if not entry_storage and ("/" in policy or "\\" in policy or policy.endswith(".jsonl")):
                entry_storage = policy.strip()
            elif not summary_sync:
                summary_sync = policy.strip()
        if not isinstance(entry_storage, str) or not entry_storage.strip():
            entry_storage = "experience/entries.jsonl"
        if not isinstance(summary_sync, str) or not summary_sync.strip():
            summary_sync = "frontmatter_experience"
        return {
            "entry_storage": entry_storage,
            "summary_sync": summary_sync,
        }

    def _normalize_skill_spec(self, skill_name, meta, spec, tool_refs, kind):
        spec = spec if isinstance(spec, dict) else {}
        spec["version"] = spec.get("version") if isinstance(spec.get("version"), int) else 2
        spec["name"] = spec.get("name") if isinstance(spec.get("name"), str) and spec.get("name").strip() else (meta.get("name") or skill_name)
        spec["kind"] = kind
        description = spec.get("description") if isinstance(spec.get("description"), str) else ""
        if not description:
            description = meta.get("description") or ""
        spec["description"] = description
        spec["capability_group"] = self._infer_capability_group(skill_name, meta, spec)
        spec["tool_refs"] = self._coerce_string_list(spec.get("tool_refs")) or list(tool_refs)
        spec["tags"] = self._coerce_string_list(spec.get("tags"))
        spec["triggers"] = self._coerce_string_list(spec.get("triggers"))
        spec["anti_triggers"] = self._coerce_string_list(spec.get("anti_triggers"))
        spec["references"] = self._coerce_string_list(spec.get("references"))
        spec["script_refs"] = self._coerce_string_list(spec.get("script_refs"))
        spec["asset_refs"] = self._coerce_string_list(spec.get("asset_refs"))
        spec["script_entries"] = self._normalize_script_entries(spec.get("script_entries"))
        spec["python_dependencies"] = self._coerce_string_list(spec.get("python_dependencies"))
        spec["node_dependencies"] = self._coerce_string_list(spec.get("node_dependencies"))
        source_format = spec.get("source_format")
        if not isinstance(source_format, str) or not source_format.strip():
            creation_hints = spec.get("creation_hints") if isinstance(spec.get("creation_hints"), dict) else {}
            source_format = creation_hints.get("source_format") if isinstance(creation_hints.get("source_format"), str) else ""
        spec["source_format"] = source_format.strip() if isinstance(source_format, str) and source_format.strip() else "cowork"
        workflow = spec.get("workflow")
        if workflow is None:
            spec["workflow"] = []
        elif not isinstance(workflow, (str, list, dict)):
            spec["workflow"] = [str(workflow)]
        spec["execution_surface"] = self._infer_execution_surface(spec)
        spec["prompt_disclosure"] = self._infer_prompt_disclosure(spec)
        spec["preferred_script_name"] = self._infer_preferred_script_name(spec)
        spec["experience_policy"] = self._normalize_experience_policy(spec)
        spec["disclosure_level_defaults"] = self._normalize_disclosure_defaults(spec)
        return spec

    def _safe_read_reference(self, base_path, ref):
        if not isinstance(ref, str):
            return ""
        ref_path = os.path.join(base_path, ref)
        if not os.path.isfile(ref_path):
            return ""
        try:
            with open(ref_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read(4000).strip()
        except Exception:
            return ""

    def _experience_entries_path(self, skill_path, spec=None):
        spec = spec or {}
        policy = self._normalize_experience_policy(spec)
        rel_path = policy.get("entry_storage") or "experience/entries.jsonl"
        return os.path.join(skill_path, rel_path)

    def _load_experience_entries(self, skill_path, spec=None):
        entries_path = self._experience_entries_path(skill_path, spec=spec)
        if not os.path.isfile(entries_path):
            return []
        entries = []
        try:
            with open(entries_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                        if isinstance(payload, dict):
                            entries.append(payload)
                    except Exception:
                        continue
        except Exception:
            return []
        return entries

    def _summarize_experience_entries(self, entries, limit=5):
        lines = []
        for entry in entries[-limit:]:
            text = (entry.get("experience_text") or "").strip()
            if not text:
                continue
            tags = []
            if entry.get("tool_name"):
                tags.append(f"tool={entry['tool_name']}")
            if entry.get("task_type"):
                tags.append(f"task={entry['task_type']}")
            if entry.get("error_pattern"):
                tags.append(f"error={entry['error_pattern']}")
            prefix = f"[{'; '.join(tags)}] " if tags else ""
            lines.append(f"- {prefix}{text}")
        return "\n".join(lines)

    def _build_skill_prompt(self, skill_name, meta, body, spec, tool_refs, experience_entries=None, include_references=False):
        experience_entries = experience_entries or []
        sections = []
        title = meta.get("name") or spec.get("name") or skill_name
        sections.append(f"# {title}")

        description = spec.get("description") or meta.get("description")
        if description:
            sections.append(description)
        if body:
            sections.append(body)

        frontmatter_exp = meta.get("experience")
        if isinstance(frontmatter_exp, str):
            frontmatter_exp = [frontmatter_exp]
        if isinstance(frontmatter_exp, list) and frontmatter_exp:
            sections.append(
                "## Learned Experience / Lessons Learned\n"
                + "\n".join(f"- {item}" for item in frontmatter_exp if item)
            )

        if experience_entries:
            entry_block = self._summarize_experience_entries(experience_entries)
            if entry_block:
                sections.append("## Recent Structured Experience\n" + entry_block)

        workflow = spec.get("workflow")
        if workflow:
            if isinstance(workflow, str):
                sections.append("## Recommended Workflow\n" + workflow)
            elif isinstance(workflow, list):
                sections.append(
                    "## Recommended Workflow\n"
                    + "\n".join(f"{index}. {item}" for index, item in enumerate(workflow, start=1))
                )
            elif isinstance(workflow, dict):
                try:
                    sections.append("## Recommended Workflow\n" + json.dumps(workflow, ensure_ascii=False, indent=2))
                except Exception:
                    pass

        if tool_refs:
            sections.append("## Recommended Tools\n" + "\n".join(f"- `{name}`" for name in tool_refs))

        script_entries = spec.get("script_entries") or []
        if script_entries:
            sections.append(
                "## Skill Scripts\n"
                + "\n".join(
                    f"- `{item['name']}` -> `{item['path']}` ({item['runtime']})"
                    + (f": {item['description']}" if item.get("description") else "")
                    for item in script_entries[:12]
                )
                + "\nUse `command-tools.run_skill_script` to execute these scripts inside the sandbox runtime."
                + "\nDo not use `glob`, `grep`, or `bash` just to locate this skill directory or script path when these entries are already listed."
            )

        dependency_lines = []
        if spec.get("python_dependencies"):
            dependency_lines.append("python: " + ", ".join(spec.get("python_dependencies")[:12]))
        if spec.get("node_dependencies"):
            dependency_lines.append("node: " + ", ".join(spec.get("node_dependencies")[:12]))
        if dependency_lines:
            sections.append("## Runtime Dependencies\n" + "\n".join(f"- {line}" for line in dependency_lines))

        if include_references:
            for ref in spec.get("references") or []:
                ref_content = self._safe_read_reference(self.loaded_skill_sources.get(skill_name, ""), ref)
                if ref_content:
                    sections.append(f"## Reference: {ref}\n{ref_content}")
        elif spec.get("references"):
            sections.append(
                "## References\n"
                + "\n".join(f"- `{ref}`" for ref in spec.get("references") if isinstance(ref, str))
            )

        return "\n\n".join([item for item in sections if item]).strip()

    def _build_brief_prompt(self, skill_name, meta, spec, tool_refs):
        lines = [f"[Experience Package] {meta.get('name') or spec.get('name') or skill_name}"]
        description = spec.get("description") or meta.get("description")
        if description:
            lines.append(f"description: {description}")
        kind = self._normalize_skill_kind(meta, spec)
        lines.append(f"kind: {kind}")
        capability_group = self._infer_capability_group(skill_name, meta, spec)
        lines.append(f"capability-group: {capability_group}")
        tags = spec.get("tags") or []
        if tags:
            lines.append(f"tags: {', '.join(tags[:8])}")
        triggers = spec.get("triggers") or []
        if triggers:
            lines.append(f"triggers: {', '.join(triggers[:6])}")
        if tool_refs:
            lines.append(f"tool-refs: {', '.join(tool_refs[:8])}")
        script_entries = spec.get("script_entries") or []
        if script_entries:
            lines.append(f"scripts: {', '.join(item.get('name') for item in script_entries[:6] if item.get('name'))}")
        script_execution_hint = self._script_execution_hint(spec)
        if script_execution_hint:
            lines.append(f"script-execution: {script_execution_hint}")
        frontmatter_exp = meta.get("experience")
        if isinstance(frontmatter_exp, list) and frontmatter_exp:
            lines.append(f"experience-highlights: {', '.join(frontmatter_exp[:3])}")
        lines.append("Full experience package is available when this skill is selected.")
        return "\n".join(lines)

    def _parse_tool_refs(self, meta, spec):
        refs = []
        raw = spec.get("tool_refs")
        if isinstance(raw, list):
            refs.extend([item for item in raw if isinstance(item, str) and item])
        elif isinstance(raw, str) and raw:
            refs.extend(self._coerce_string_list(raw))
        allowed = meta.get("allowed-tools")
        if isinstance(allowed, list):
            refs.extend([item for item in allowed if isinstance(item, str) and item])
        elif isinstance(allowed, str) and allowed:
            refs.extend(self._coerce_string_list(allowed) or [allowed])
        deduped = []
        seen = set()
        for item in refs:
            if item not in seen:
                deduped.append(item)
                seen.add(item)
        return deduped

    def _register_tool(self, skill_name, tool_name, func, description=None, tool_kind="legacy_function"):
        if tool_name in self.tools:
            print(
                f"[SkillManager] Duplicate tool '{tool_name}' from skill '{skill_name}' skipped; already provided by '{self.tool_to_skill_map.get(tool_name)}'."
            )
            return

        sig = inspect.signature(func)
        properties = {}
        required = []
        for param_name, param in sig.parameters.items():
            if param_name in {"workspace_dir", "_context"}:
                continue
            param_type = "string"
            if param.default != inspect.Parameter.empty:
                if isinstance(param.default, bool):
                    param_type = "boolean"
                elif isinstance(param.default, int):
                    param_type = "integer"
                elif isinstance(param.default, list):
                    param_type = "array"
            if param_name == "tasks":
                param_type = "array"
            elif param_name in {"limit", "offset", "priority"}:
                param_type = "integer"
            elif param_name in {"recursive"}:
                param_type = "boolean"
            prop_def = {"type": param_type, "description": "Parameter"}
            if param_type == "array":
                prop_def["items"] = {"type": "string"}
            properties[param_name] = prop_def
            if param.default == inspect.Parameter.empty:
                required.append(param_name)

        tool_description = description or (func.__doc__.strip().split("\n")[0] if func.__doc__ else f"Tool {tool_name}")
        parameters_schema = {"type": "object", "properties": properties, "required": required}
        record = self.tool_registry.register(
            tool_name,
            func,
            tool_description,
            parameters_schema,
            skill_name=skill_name,
            kind=tool_kind,
            runtime_binding={"type": "python_function", "skill_name": skill_name},
            skill_refs=[skill_name],
        )
        if not record:
            return

        self.tools[tool_name] = func
        self.tool_to_skill_map[tool_name] = skill_name
        self.skill_to_tools.setdefault(skill_name, []).append(tool_name)
        self.tool_definitions.append(
            record.to_definition()
        )
        self.tool_records[tool_name] = {
            "name": tool_name,
            "description": tool_description,
            "kind": tool_kind,
            "parameters_schema": parameters_schema,
            "aliases": list(record.aliases),
            "search_hint": record.search_hint,
            "read_only": record.read_only,
            "destructive": record.destructive,
            "allowed_modes": sorted(record.allowed_modes),
            "should_defer": record.should_defer,
            "always_load": record.always_load,
            "runtime_binding": {"type": "python_function", "skill_name": skill_name},
            "skill_refs": [skill_name],
        }

    def _register_explicit_tool_export(self, skill_name, export):
        if not isinstance(export, dict):
            return
        tool_name = str(export.get("name") or "").strip()
        func = export.get("handler")
        if not tool_name or not callable(func):
            return
        if tool_name in self.tools:
            print(
                f"[SkillManager] Duplicate explicit tool '{tool_name}' from skill '{skill_name}' skipped; already provided by '{self.tool_to_skill_map.get(tool_name)}'."
            )
            return

        description = str(export.get("description") or f"Tool {tool_name}").strip()
        parameters_schema = export.get("parameters")
        if not isinstance(parameters_schema, dict):
            parameters_schema = {"type": "object", "properties": {}, "required": []}
        parameters_schema.setdefault("type", "object")
        parameters_schema.setdefault("properties", {})
        parameters_schema.setdefault("required", [])

        record = self.tool_registry.register(
            tool_name,
            func,
            description,
            parameters_schema,
            skill_name=skill_name,
            kind=str(export.get("kind") or "explicit_tool"),
            aliases=export.get("aliases"),
            search_hint=export.get("search_hint") or export.get("searchHint") or "",
            read_only=export.get("read_only") if "read_only" in export else export.get("readOnly"),
            destructive=export.get("destructive"),
            allowed_modes=export.get("allowed_modes") or export.get("allowedModes"),
            should_defer=export.get("should_defer") if "should_defer" in export else export.get("shouldDefer"),
            always_load=export.get("always_load") if "always_load" in export else export.get("alwaysLoad"),
            runtime_binding={
                **{
                    "type": "python_function",
                    "skill_name": skill_name,
                    "export_name": tool_name,
                },
                **(
                    dict(export.get("runtime_binding"))
                    if isinstance(export.get("runtime_binding"), dict)
                    else {}
                ),
            },
            skill_refs=[skill_name],
            metadata={
                "requires_user_interaction": bool(export.get("requires_user_interaction")),
                "result_format": str(export.get("result_format") or ""),
                **(dict(export.get("metadata")) if isinstance(export.get("metadata"), dict) else {}),
            },
        )
        if not record:
            return

        self.tools[tool_name] = func
        self.tool_to_skill_map[tool_name] = skill_name
        self.skill_to_tools.setdefault(skill_name, []).append(tool_name)
        self.tool_definitions.append(record.to_definition())
        self.tool_records[tool_name] = {
            "name": tool_name,
            "description": description,
            "kind": str(export.get("kind") or "explicit_tool"),
            "parameters_schema": parameters_schema,
            "aliases": list(record.aliases),
            "search_hint": record.search_hint,
            "read_only": record.read_only,
            "destructive": record.destructive,
            "allowed_modes": sorted(record.allowed_modes),
            "should_defer": record.should_defer,
            "always_load": record.always_load,
            "runtime_binding": {
                **{
                    "type": "python_function",
                    "skill_name": skill_name,
                    "export_name": tool_name,
                },
                **(
                    dict(export.get("runtime_binding"))
                    if isinstance(export.get("runtime_binding"), dict)
                    else {}
                ),
            },
            "skill_refs": [skill_name],
            "requires_user_interaction": bool(export.get("requires_user_interaction")),
            "result_format": str(export.get("result_format") or ""),
            "metadata": dict(export.get("metadata")) if isinstance(export.get("metadata"), dict) else {},
        }

    def _load_legacy_implementation(self, skill_name, impl_path):
        try:
            skill_spec = self._load_skill_json(os.path.dirname(impl_path))
            declared_python_dependencies = self._coerce_string_list(skill_spec.get("python_dependencies"))
            python_path = build_sandbox_env(skill_id=skill_name).get("PYTHONPATH", "")
            for path in reversed([item for item in python_path.split(os.pathsep) if item]):
                if os.path.isdir(path) and path not in sys.path:
                    sys.path.insert(0, path)
            spec = importlib.util.spec_from_file_location(f"skills.{skill_name}", impl_path)
            if spec is None or spec.loader is None:
                raise ImportError(f"Unable to load module spec for {impl_path}")
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except ImportError as e:
                missing_pkg = getattr(e, "name", None)
                if missing_pkg and not declared_python_dependencies:
                    ensure_package_installed(missing_pkg, skill_id=skill_name)
                    spec.loader.exec_module(module)
                else:
                    raise e
            explicit_exports = getattr(module, "TOOL_EXPORTS", None)
            if explicit_exports is None:
                explicit_exports = getattr(module, "TOOLS", None)
            exported_handler_names = set()
            if isinstance(explicit_exports, list):
                for export in explicit_exports:
                    self._register_explicit_tool_export(skill_name, export)
                    handler = export.get("handler") if isinstance(export, dict) else None
                    handler_name = getattr(handler, "__name__", None)
                    if isinstance(handler_name, str) and handler_name:
                        exported_handler_names.add(handler_name)
            for name, func in inspect.getmembers(module, inspect.isfunction):
                if name.startswith("_"):
                    continue
                if getattr(func, "__module__", None) != getattr(module, "__name__", None):
                    continue
                if name in exported_handler_names:
                    continue
                tool_kind = "system_entry" if skill_name in self.SYSTEM_SKILLS else "legacy_function"
                self._register_tool(skill_name, name, func, tool_kind=tool_kind)
        except Exception as e:
            print(f"Error loading implementation {impl_path}: {e}")

    def _register_builtin_tools(self):
        search_parameters_schema = {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keywords describing the tool or capability to discover.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of matching tools to return.",
                },
                "include_loaded": {
                    "type": "boolean",
                    "description": "Whether to include tools already loaded in this run.",
                },
            },
            "required": ["query"],
        }
        record = self.tool_registry.register(
            "tool_search",
            self._tool_search,
            "Search deferred tools by keyword and make matched tools available on the next turn.",
            search_parameters_schema,
            skill_name="builtin",
            kind="builtin_discovery",
            search_hint="discover deferred tools capabilities",
            read_only=True,
            destructive=False,
            allowed_modes=["clarifying", "execution"],
            should_defer=False,
            always_load=True,
            runtime_binding={"type": "builtin_method"},
            skill_refs=["builtin"],
        )
        if record:
            self.tools["tool_search"] = self._tool_search
            self.tool_records["tool_search"] = {
                "name": record.name,
                "description": record.description,
                "kind": record.kind,
                "parameters_schema": record.parameters_schema,
                "aliases": list(record.aliases),
                "search_hint": record.search_hint,
                "read_only": record.read_only,
                "destructive": record.destructive,
                "allowed_modes": sorted(record.allowed_modes),
                "should_defer": record.should_defer,
                "always_load": record.always_load,
                "runtime_binding": record.runtime_binding,
                "skill_refs": list(record.skill_refs),
            }

    def _tool_search(self, query, limit=8, include_loaded=False, _context=None):
        context = _context if isinstance(_context, dict) else {}
        run_context = context.get("run_context") if isinstance(context.get("run_context"), dict) else {}
        run_mode = run_context.get("mode")
        discovered = context.get("discovered_tool_names")
        if discovered is None:
            discovered = set()
        results = self.tool_registry.search(
            query,
            run_mode=run_mode,
            limit=limit,
            include_loaded=bool(include_loaded),
            discovered_tool_names=discovered,
        )
        results = self._filter_results_by_allowed_skills(results, run_context)
        results = self._filter_enterprise_tool_results(results, run_context)
        skill_results = self._search_skills(query, limit=limit, run_context=run_context)
        if (
            self._is_enterprise_tool_allowed("publish_artifacts", run_context)
            and self._is_tool_allowed_by_skill_scope("publish_artifacts", run_context)
        ):
            loaded_matches = self.tool_registry.search(
                query,
                run_mode=run_mode,
                limit=max(int(limit or 8), 1),
                include_loaded=True,
                discovered_tool_names=discovered,
            )
            loaded_matches = self._filter_results_by_allowed_skills(loaded_matches, run_context)
            for item in loaded_matches:
                if item.get("name") != "publish_artifacts":
                    continue
                if any(existing.get("name") == "publish_artifacts" for existing in results):
                    break
                results.append(item)
                break
            results.sort(key=lambda item: (-float(item.get("score") or 0), item.get("name") or ""))
            results = results[: max(1, int(limit or 8))]
        names = [item["name"] for item in results]
        if hasattr(discovered, "update"):
            discovered.update(names)
        elif isinstance(discovered, list):
            for name in names:
                if name not in discovered:
                    discovered.append(name)
        return {
            "status": "ok",
            "query": query,
            "count": len(results),
            "skill_count": len(skill_results),
            "discovered_tools": names,
            "tools": results,
            "skills": skill_results,
            "message": "Matched tools will be available on the next model turn.",
        }

    def _skill_search_score(self, record, query_tokens, query_text):
        spec = record.get("spec") or {}
        meta = record.get("meta") or {}
        search_text = "\n".join(
            [
                record.get("name", ""),
                meta.get("display_name", ""),
                meta.get("name", ""),
                spec.get("description", ""),
                meta.get("description", ""),
                record.get("body", ""),
                record.get("search_text", ""),
            ]
        )
        search_tokens = _tokenize(search_text)
        if not search_tokens:
            return 0.0
        score = 0.0
        explicit_tokens = _tokenize(
            " ".join(
                [
                    record.get("name", ""),
                    meta.get("display_name", ""),
                    meta.get("name", ""),
                    " ".join(spec.get("tags") or []),
                    " ".join(spec.get("triggers") or []),
                ]
            )
        )
        normalized_query = _normalize_search_text(query_text)
        normalized_text = _normalize_search_text(search_text)
        if normalized_query and normalized_text and normalized_query in normalized_text:
            score += 10.0
        score += len(query_tokens & explicit_tokens) * 8.0
        score += len(query_tokens & search_tokens) * 3.0
        anti_tokens = _tokenize(" ".join(spec.get("anti_triggers") or []))
        score -= len(query_tokens & anti_tokens) * 20.0
        return score

    def _search_skills(self, query, limit=8, run_context=None):
        query_tokens = _tokenize(query)
        if not query_tokens and not _normalize_search_text(query):
            return []
        matches = []
        for skill_name, record in self.skill_records.items():
            if not self._is_skill_allowed_by_scope(skill_name, run_context):
                continue
            score = self._skill_search_score(record, query_tokens, query)
            if score <= 0:
                continue
            matches.append((score, skill_name, record))
        matches.sort(key=lambda item: (-item[0], item[1]))
        max_results = max(1, int(limit or 8))
        return [self._skill_search_payload(record, score) for score, _name, record in matches[:max_results]]

    def _skill_search_payload(self, record, score):
        spec = record.get("spec") or {}
        meta = record.get("meta") or {}
        execution_surface = spec.get("execution_surface") or self._infer_execution_surface(spec)
        preferred_script_name = spec.get("preferred_script_name") or self._infer_preferred_script_name(spec)
        prompt_disclosure = spec.get("prompt_disclosure") or self._infer_prompt_disclosure(spec)
        script_entries = list(spec.get("script_entries") or [])
        return {
            "name": record.get("name") or "",
            "display_name": meta.get("display_name") or meta.get("name") or record.get("name") or "",
            "description": spec.get("description") or meta.get("description") or "",
            "kind": record.get("kind") or spec.get("kind") or "knowledge",
            "capability_group": spec.get("capability_group") or "",
            "source_format": spec.get("source_format") or "",
            "tool_refs": list(record.get("tool_refs") or []),
            "script_entries": script_entries,
            "execution_surface": execution_surface,
            "prompt_level": "full" if prompt_disclosure == "full_on_match" else "brief",
            "preferred_tool": "run_skill_script" if execution_surface == "skill_script" and script_entries else "",
            "preferred_skill_name": record.get("name") or "",
            "preferred_script_name": preferred_script_name,
            "script_candidates": [
                item.get("name") for item in script_entries if isinstance(item.get("name"), str) and item.get("name").strip()
            ],
            "execution_hint": self._script_execution_hint(spec),
            "score": round(float(score), 3),
        }

    def is_tool_allowed(self, name, run_mode):
        return self.tool_registry.is_allowed(name, run_mode)

    def is_tool_visible(self, name, run_mode, discovered_tool_names=None, run_context=None):
        if not self._is_enterprise_tool_allowed(name, run_context):
            return False
        if name == "publish_artifacts":
            return True
        if not self._is_tool_allowed_by_skill_scope(name, run_context):
            return False
        return self.tool_registry.is_visible(name, run_mode, discovered_tool_names)

    def get_tool_record(self, name):
        record = self.tool_registry.get(name)
        if not record:
            return None
        return self.tool_records.get(record.name)

    def _build_minimal_skill_record(self, skill_name, skill_path, tool_refs):
        body = (
            "This legacy skill exposes tools from impl.py. Use the documented tools directly; "
            "treat this skill as a structured experience package around those tools."
        )
        meta = {
            "name": skill_name,
            "description": f"Legacy experience package wrapping tools from {skill_name}.",
            "allowed-tools": tool_refs,
        }
        spec = {
            "version": 2,
            "name": skill_name,
            "kind": "knowledge",
            "description": meta["description"],
            "capability_group": self.GROUP_DEFAULTS.get(skill_name, "knowledge"),
            "tool_refs": tool_refs,
            "experience_policy": {"entry_storage": "experience/entries.jsonl", "summary_sync": "frontmatter_experience"},
            "disclosure_level_defaults": {
                "default_prompt_level": "brief",
                "include_references": False,
                "include_experience_entries": False,
            },
        }
        entries = self._load_experience_entries(skill_path, spec=spec)
        prompt = self._build_skill_prompt(skill_name, meta, body, spec, tool_refs, experience_entries=entries)
        return {
            "name": skill_name,
            "path": skill_path,
            "kind": "knowledge",
            "meta": meta,
            "spec": spec,
            "tool_refs": tool_refs,
            "body": body,
            "experience_entries": entries,
            "brief": self._build_brief_prompt(skill_name, meta, spec, tool_refs),
            "prompt": prompt,
            "search_text": "\n".join([skill_name, meta["description"], body, " ".join(tool_refs), self._summarize_experience_entries(entries)]),
        }

    def _build_mcp_skill_record(self, skill_name, server_config, tool_refs, dependency_status):
        server_name = str(server_config.get("name") or server_config.get("id") or "MCP Server").strip()
        transport = mcp_transport_label(server_config.get("transport"))
        summary = summarize_mcp_server(server_config)
        body = (
            "# Skill Purpose\n"
            "Expose tools from a configured MCP server.\n\n"
            "## Interface Details\n"
            f"- Transport: `{transport}`\n"
            f"- Endpoint: `{summary}`\n"
            "- Tools are discovered through `tool_search` and called like normal tools.\n"
        )
        meta = {
            "name": skill_name,
            "display_name": f"MCP / {server_name}",
            "description": f"Configured MCP server '{server_name}' exposed as external tools.",
            "description_cn": f"通过 MCP 协议接入的外部工具服务器：{server_name}。",
            "created_by": "system",
            "kind": "knowledge",
            "allowed-tools": tool_refs,
            "source_format": self.MCP_SOURCE_FORMAT,
            "security_level": "medium",
        }
        spec = {
            "version": 2,
            "name": skill_name,
            "kind": "knowledge",
            "capability_group": "external-mcp",
            "description": f"Configured MCP server '{server_name}' exposed as external tools.",
            "description_cn": f"通过 MCP 协议接入的外部工具服务器：{server_name}。",
            "tags": ["mcp", "external tools", transport, server_name],
            "triggers": ["mcp", server_name, "external tool", "protocol server"],
            "anti_triggers": [],
            "tool_refs": tool_refs,
            "references": [],
            "script_refs": [],
            "script_entries": [],
            "asset_refs": [],
            "python_dependencies": ["mcp"],
            "node_dependencies": [],
            "source_format": self.MCP_SOURCE_FORMAT,
            "disclosure_level_defaults": {
                "default_prompt_level": "brief",
                "include_references": False,
                "include_experience_entries": False,
            },
            "workflow": [
                "Use tool_search to discover the MCP server tools you need.",
                "Call the discovered MCP tools directly from the agent loop.",
            ],
        }
        prompt = self._build_skill_prompt(skill_name, meta, body, spec, tool_refs, experience_entries=[], include_references=False)
        return {
            "name": skill_name,
            "path": f"mcp://{server_config.get('id') or skill_name}",
            "kind": "knowledge",
            "meta": meta,
            "spec": spec,
            "tool_refs": list(tool_refs),
            "body": body,
            "experience_entries": [],
            "brief": self._build_brief_prompt(skill_name, meta, spec, tool_refs),
            "prompt": prompt,
            "search_text": "\n".join([skill_name, server_name, transport, summary, " ".join(tool_refs), body]),
            "dependency_status": dict(dependency_status or {"ok": True, "message": "MCP server configured."}),
        }

    def _register_mcp_tools_for_server(self, skill_name, server_config, tools_payload):
        tool_refs = []
        used_names = set(self.tools)
        for tool in tools_payload or []:
            remote_name = str(tool.get("name") or "").strip()
            if not remote_name:
                continue
            local_name = build_mcp_tool_name(server_config.get("id"), remote_name)
            base_name = local_name
            suffix = 2
            while local_name in used_names:
                local_name = f"{base_name}_{suffix}"
                suffix += 1
            used_names.add(local_name)

            def _handler(_arguments=None, _server=json.loads(json.dumps(server_config, ensure_ascii=False)), _remote_name=remote_name, **kwargs):
                payload = dict(_arguments or {})
                payload.update(kwargs)
                return call_mcp_tool(_server, _remote_name, payload)

            export = {
                "name": local_name,
                "handler": _handler,
                "description": str(tool.get("description") or f"MCP tool '{remote_name}' from server '{server_config.get('name') or server_config.get('id')}'.").strip(),
                "parameters": dict(tool.get("input_schema") or {"type": "object", "properties": {}, "required": []}),
                "kind": "mcp_tool",
                "aliases": [
                    f"{server_config.get('id') or server_config.get('name')}.{remote_name}",
                    f"{server_config.get('name') or server_config.get('id')}::{remote_name}",
                ],
                "search_hint": " ".join(
                    part for part in [
                        "mcp",
                        str(server_config.get("name") or ""),
                        str(server_config.get("id") or ""),
                        remote_name,
                        str(tool.get("description") or ""),
                    ]
                    if part
                ),
                "allowed_modes": ["execution"],
                "should_defer": True,
                "always_load": False,
                "metadata": {
                    "mcp_server_id": str(server_config.get("id") or ""),
                    "mcp_server_name": str(server_config.get("name") or ""),
                    "mcp_tool_name": remote_name,
                    "mcp_transport": str(server_config.get("transport") or ""),
                },
            }
            self._register_explicit_tool_export(skill_name, export)
            if local_name in self.tools:
                tool_refs.append(local_name)
        return tool_refs

    def _load_mcp_servers(self):
        if not self.config_manager or not hasattr(self.config_manager, "get_mcp_servers"):
            return
        servers = self.config_manager.get_mcp_servers()
        if not isinstance(servers, list):
            return
        package_ready = mcp_package_available()
        for index, server_config in enumerate(servers):
            if not isinstance(server_config, dict):
                continue
            skill_name = build_mcp_skill_name(server_config.get("id") or server_config.get("name") or f"server-{index + 1}")
            if self.config_manager and not self.config_manager.is_skill_enabled(skill_name):
                continue
            tool_refs = []
            dependency_status = {"ok": True, "message": "MCP server is available."}
            if not bool(server_config.get("enabled", True)):
                dependency_status = {"ok": False, "message": "MCP server is disabled in settings."}
            elif not package_ready:
                dependency_status = {"ok": False, "message": "Python package 'mcp' is not installed."}
            else:
                result = list_mcp_server_tools(server_config)
                if result.get("ok"):
                    tool_refs = self._register_mcp_tools_for_server(skill_name, server_config, result.get("tools"))
                    dependency_status = {
                        "ok": True,
                        "message": f"Loaded {len(tool_refs)} MCP tools.",
                    }
                else:
                    dependency_status = {
                        "ok": False,
                        "message": result.get("error") or "Failed to connect to MCP server.",
                    }
            record = self._build_mcp_skill_record(skill_name, server_config, tool_refs, dependency_status)
            self.loaded_skills_meta[skill_name] = record["meta"]
            self.skill_prompts_brief.append(record["brief"])
            self.skill_prompts_full[skill_name] = record["prompt"]
            self.skill_records[skill_name] = record
            self.skill_to_tools.setdefault(skill_name, list(tool_refs))

    def _load_skill_record(self, skill_name, skill_path):
        md_path = os.path.join(skill_path, "SKILL.md")
        meta, body = self._parse_skill_md_content(md_path) if os.path.exists(md_path) else ({}, "")
        spec = self._load_skill_json(skill_path)
        tool_refs = self._parse_tool_refs(meta, spec)
        kind = self._normalize_skill_kind(meta, spec)
        if not meta and not spec:
            return self._build_minimal_skill_record(skill_name, skill_path, tool_refs)
        spec = self._normalize_skill_spec(skill_name, meta, spec, tool_refs, kind)
        explicit_references = list(spec.get("references") or [])
        artifact_info = discover_skill_artifacts(
            skill_path,
            declared_script_entries=spec.get("script_entries"),
            declared_script_refs=spec.get("script_refs"),
            declared_asset_refs=spec.get("asset_refs"),
            declared_references=spec.get("references"),
        )
        spec["script_refs"] = artifact_info.get("script_refs") or list(spec.get("script_refs") or [])
        spec["script_entries"] = artifact_info.get("script_entries") or list(spec.get("script_entries") or [])
        spec["asset_refs"] = artifact_info.get("asset_refs") or list(spec.get("asset_refs") or [])
        spec["references"] = explicit_references or artifact_info.get("references") or []
        tool_refs = list(spec.get("tool_refs") or [])
        spec["execution_surface"] = self._infer_execution_surface(spec)
        spec["prompt_disclosure"] = self._infer_prompt_disclosure(spec)
        spec["preferred_script_name"] = self._infer_preferred_script_name(spec)
        experience_entries = self._load_experience_entries(skill_path, spec=spec)
        record = {
            "name": skill_name,
            "path": skill_path,
            "kind": kind,
            "meta": meta,
            "spec": spec,
            "tool_refs": tool_refs,
            "body": body,
            "experience_entries": experience_entries,
        }
        record["brief"] = self._build_brief_prompt(skill_name, meta, spec, tool_refs)
        record["prompt"] = self._build_skill_prompt(
            skill_name,
            meta,
            body,
            spec,
            tool_refs,
            experience_entries=experience_entries if spec["disclosure_level_defaults"].get("include_experience_entries") else [],
            include_references=False,
        )
        record["search_text"] = "\n".join(
            [
                skill_name,
                meta.get("display_name", ""),
                meta.get("description", ""),
                spec.get("description", ""),
                body,
                " ".join(spec.get("tags") or []),
                " ".join(spec.get("triggers") or []),
                " ".join(tool_refs),
                " ".join(spec.get("script_refs") or []),
                " ".join(item.get("name", "") for item in spec.get("script_entries") or []),
                " ".join(item.get("runtime", "") for item in spec.get("script_entries") or []),
                " ".join(os.path.basename(ref) for ref in spec.get("references") or [] if isinstance(ref, str)),
                " ".join(spec.get("python_dependencies") or []),
                " ".join(spec.get("node_dependencies") or []),
                self._summarize_experience_entries(experience_entries, limit=10),
            ]
        )
        return record

    def _prepare_skill_dependencies(self, skill_name, skill_path):
        spec = self._load_skill_json(skill_path)
        if not isinstance(spec, dict):
            return {"ok": True, "message": "No dependency metadata."}
        python_dependencies = self._coerce_string_list(spec.get("python_dependencies"))
        node_dependencies = self._coerce_string_list(spec.get("node_dependencies"))
        if not python_dependencies and not node_dependencies:
            return {"ok": True, "message": "No dependencies declared."}
        status = install_skill_dependencies(
            skill_name,
            python_dependencies=python_dependencies,
            node_dependencies=node_dependencies,
        )
        if not status.get("ok"):
            print(f"[SkillManager] Dependencies for '{skill_name}' are not ready: {status.get('message')}")
        return status

    def _default_writable_skill_root(self):
        for candidate in self.skills_dirs:
            if os.path.basename(candidate) == "ai_skills":
                try:
                    os.makedirs(candidate, exist_ok=True)
                    return candidate
                except Exception:
                    continue
        candidate = self.skills_dirs[0]
        os.makedirs(candidate, exist_ok=True)
        return candidate

    def _find_skill_path(self, skill_name):
        normalized_name = str(skill_name or "").strip()
        if not normalized_name:
            return None
        for skills_dir in self.skills_dirs:
            candidate = os.path.join(skills_dir, normalized_name)
            if os.path.isdir(candidate):
                return candidate
        return None

    def _resolve_skill_file_path(self, skill_name, relative_path, *, require_writable=False):
        skill_path = self._find_skill_path(skill_name)
        if not skill_path:
            return None, None, f"Skill '{skill_name}' not found."
        root = os.path.abspath(skill_path)
        if require_writable and os.path.basename(os.path.dirname(root)) != "ai_skills":
            return root, None, "Only user skills in ai_skills can be edited."
        rel = str(relative_path or "").strip().replace("\\", os.sep).replace("/", os.sep)
        if not rel:
            return root, None, "File path is required."
        parts = [part for part in rel.split(os.sep) if part]
        if any(part in {"..", "."} for part in parts):
            return root, None, "File path cannot contain relative traversal."
        if parts and parts[0] in EXCLUDED_DIRS:
            return root, None, "Cache and build directories cannot be edited from Skill Center."
        resolved = os.path.abspath(os.path.join(root, *parts))
        try:
            if os.path.commonpath([root, resolved]) != root:
                return root, None, "File path is outside the skill directory."
        except ValueError:
            return root, None, "File path is outside the skill directory."
        return root, resolved, ""

    def is_skill_editable(self, skill_name):
        skill_path = self._find_skill_path(skill_name)
        return bool(skill_path and os.path.basename(os.path.dirname(os.path.abspath(skill_path))) == "ai_skills")

    def _find_user_skill_path(self, skill_name):
        normalized_name = str(skill_name or "").strip()
        if not normalized_name:
            return None
        for skills_dir in self.skills_dirs:
            root = os.path.abspath(skills_dir)
            if os.path.basename(root) != "ai_skills":
                continue
            candidate = os.path.abspath(os.path.join(root, normalized_name))
            try:
                if os.path.commonpath([root, candidate]) != root:
                    continue
            except ValueError:
                continue
            if os.path.isdir(candidate):
                return candidate
        return None

    def list_skill_files(self, skill_name):
        skill_path = self._find_skill_path(skill_name)
        if not skill_path:
            return {"ok": False, "error": f"Skill '{skill_name}' not found.", "files": [], "editable": False}
        files = []
        for root, dirs, filenames in os.walk(skill_path):
            dirs[:] = [name for name in dirs if name not in EXCLUDED_DIRS and not name.startswith(".")]
            rel_root = os.path.relpath(root, skill_path)
            for filename in filenames:
                if filename == ".DS_Store":
                    continue
                rel_path = filename if rel_root == "." else os.path.join(rel_root, filename)
                files.append(rel_path.replace("\\", "/"))
        files.sort(key=lambda item: (0 if item in {"SKILL.md", "skill.json", "impl.py"} else 1, item.lower()))
        return {"ok": True, "error": "", "files": files, "editable": self.is_skill_editable(skill_name)}

    def read_skill_file(self, skill_name, relative_path):
        _root, path, error = self._resolve_skill_file_path(skill_name, relative_path)
        if error:
            return {"ok": False, "error": error, "content": ""}
        if not os.path.isfile(path):
            return {"ok": False, "error": "File not found.", "content": ""}
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                return {"ok": True, "error": "", "content": f.read()}
        except UnicodeDecodeError:
            return {"ok": False, "error": "Only UTF-8 text files can be edited here.", "content": ""}
        except Exception as e:
            return {"ok": False, "error": str(e), "content": ""}

    def write_skill_file(self, skill_name, relative_path, content):
        _root, path, error = self._resolve_skill_file_path(skill_name, relative_path, require_writable=True)
        if error:
            return {"ok": False, "error": error}
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(str(content or ""))
            validation = self.validate_skill(skill_name)
            self.load_skills()
            if not validation.get("ok"):
                return {"ok": True, "error": "", "validation": validation}
            return {"ok": True, "error": "", "validation": validation}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def validate_skill(self, skill_name):
        skill_path = self._find_skill_path(skill_name)
        if not skill_path:
            return {"ok": False, "issues": [f"Skill '{skill_name}' not found."]}
        issues = []
        md_path = os.path.join(skill_path, "SKILL.md")
        if not os.path.isfile(md_path):
            issues.append("SKILL.md is missing.")
        skill_json_path = os.path.join(skill_path, "skill.json")
        spec = {}
        if os.path.isfile(skill_json_path):
            try:
                with open(skill_json_path, "r", encoding="utf-8-sig") as f:
                    payload = json.load(f)
                if not isinstance(payload, dict):
                    issues.append("skill.json must contain a JSON object.")
                else:
                    spec = payload
            except Exception as e:
                issues.append(f"skill.json is invalid: {e}")
        impl_path = os.path.join(skill_path, "impl.py")
        if os.path.isfile(impl_path):
            try:
                with open(impl_path, "r", encoding="utf-8-sig") as f:
                    ast.parse(f.read(), filename=impl_path)
            except Exception as e:
                issues.append(f"impl.py syntax check failed: {e}")
        for ref_key in ("references", "script_refs", "asset_refs"):
            for ref in spec.get(ref_key) or []:
                if isinstance(ref, str) and ref.strip() and not os.path.exists(os.path.join(skill_path, ref)):
                    issues.append(f"{ref_key} entry not found: {ref}")
        for entry in spec.get("script_entries") or []:
            if not isinstance(entry, dict):
                issues.append("script_entries entries must be objects.")
                continue
            rel_path = str(entry.get("path") or "").strip()
            if rel_path and not os.path.isfile(os.path.join(skill_path, rel_path)):
                issues.append(f"script entry path not found: {rel_path}")
        return {"ok": not issues, "issues": issues}

    def _read_skill_name_from_path(self, source_path):
        skill_json_path = os.path.join(source_path, "skill.json")
        if os.path.isfile(skill_json_path):
            try:
                with open(skill_json_path, "r", encoding="utf-8-sig") as f:
                    payload = json.load(f)
                skill_name = str((payload or {}).get("name") or "").strip()
                if skill_name:
                    return skill_name
            except Exception:
                pass
        skill_md_path = os.path.join(source_path, "SKILL.md")
        if os.path.isfile(skill_md_path):
            meta, _body = self._parse_skill_md_content(skill_md_path)
            skill_name = str(meta.get("name") or "").strip()
            if skill_name:
                return skill_name
        return os.path.basename(os.path.normpath(source_path))

    def _extract_zip_to_tempdir(self, source_path):
        temp_dir = tempfile.mkdtemp(prefix="cowork-skill-import-")
        try:
            with zipfile.ZipFile(source_path, "r") as archive:
                for member in archive.infolist():
                    target_path = os.path.abspath(os.path.join(temp_dir, member.filename))
                    if os.path.commonpath([temp_dir, target_path]) != temp_dir:
                        raise ValueError("ZIP contains unsafe paths")
                archive.extractall(temp_dir)
            return temp_dir
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    def _resolve_import_source_dir(self, extracted_root):
        if not os.path.isdir(extracted_root):
            raise ValueError("Extracted skill package is not a directory")
        entries = [
            entry for entry in os.listdir(extracted_root)
            if entry not in {".DS_Store", "__MACOSX"}
        ]
        markers = {"SKILL.md", "skill.json", "impl.py", "scripts", "assets", "references", "experience"}
        if any(entry in markers for entry in entries):
            return extracted_root
        child_dirs = [entry for entry in entries if os.path.isdir(os.path.join(extracted_root, entry))]
        if len(child_dirs) == 1:
            return os.path.join(extracted_root, child_dirs[0])
        if child_dirs:
            return extracted_root
        raise ValueError("ZIP must contain a skill folder, skill collection, or skill files at the root")

    def _format_import_summary_message(self, summary):
        imported = summary.get("imported") or []
        skipped = summary.get("skipped_existing") or []
        failed = summary.get("failed") or []
        lines = [
            f"导入完成：{len(imported)} 个成功，{len(skipped)} 个跳过，{len(failed)} 个失败。"
        ]
        if imported:
            lines.append("成功：" + "、".join(item.get("skill_name") or "" for item in imported[:8] if item.get("skill_name")))
        if skipped:
            lines.append("跳过：" + "、".join(item.get("skill_name") or "" for item in skipped[:8] if item.get("skill_name")))
        if failed:
            lines.append("失败：" + "、".join(item.get("skill_name") or "" for item in failed[:8] if item.get("skill_name")))
        return "\n".join(lines)

    def _import_single_skill_dir(self, source_path, source_format="auto"):
        skill_name = self._read_skill_name_from_path(source_path)
        target_dir = self._default_writable_skill_root()
        target_path = os.path.join(target_dir, skill_name)
        if os.path.exists(target_path):
            return {
                "status": "skipped_existing",
                "skill_name": skill_name,
                "message": f"Skill '{skill_name}' already exists",
            }
        result = adapt_skill_directory(source_path, target_path, skill_name=skill_name, source_format=source_format)
        dependency_status = self._prepare_skill_dependencies(skill_name, target_path)
        message = result.get("message") or f"Skill '{skill_name}' imported successfully"
        if not dependency_status.get("ok"):
            message = f"{message}\nDependency setup incomplete: {dependency_status.get('message')}"
        return {
            "status": "imported",
            "skill_name": skill_name,
            "message": message,
        }

    def export_skill(self, skill_name, destination_zip_path):
        skill_path = self._find_skill_path(skill_name)
        if not skill_path:
            return False, f"Skill '{skill_name}' not found."
        destination_zip_path = str(destination_zip_path or "").strip()
        if not destination_zip_path:
            return False, "Destination ZIP path is required."
        destination_dir = os.path.dirname(destination_zip_path) or "."
        try:
            os.makedirs(destination_dir, exist_ok=True)
            with zipfile.ZipFile(destination_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                self._write_skill_archive_entries(archive, skill_path)
            return True, f"Skill '{skill_name}' exported to {destination_zip_path}"
        except Exception as e:
            return False, f"Export failed: {e}"

    def _write_skill_archive_entries(self, archive, skill_path, archive_root=None):
        archive_root = archive_root or os.path.basename(os.path.normpath(skill_path))
        for root, dirs, filenames in os.walk(skill_path):
            dirs[:] = [name for name in dirs if name not in EXCLUDED_DIRS]
            rel_root = os.path.relpath(root, skill_path)
            for filename in filenames:
                if filename == ".DS_Store":
                    continue
                source_file = os.path.join(root, filename)
                if rel_root == ".":
                    archive_path = os.path.join(archive_root, filename)
                else:
                    archive_path = os.path.join(archive_root, rel_root, filename)
                archive.write(source_file, arcname=archive_path)

    def export_skill_collection(self, skill_names, destination_zip_path):
        names = []
        seen = set()
        for skill_name in skill_names or []:
            normalized = str(skill_name or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            names.append(normalized)
        if not names:
            return False, "Please select at least one skill to export."

        resolved = []
        skipped = []
        for skill_name in names:
            skill_path = self._find_skill_path(skill_name)
            if skill_path and os.path.isdir(skill_path):
                resolved.append((skill_name, skill_path))
            else:
                skipped.append(skill_name)
        if not resolved:
            return False, "No exportable skill directories were found."

        destination_zip_path = str(destination_zip_path or "").strip()
        if not destination_zip_path:
            return False, "Destination ZIP path is required."
        destination_dir = os.path.dirname(destination_zip_path) or "."
        try:
            os.makedirs(destination_dir, exist_ok=True)
            with zipfile.ZipFile(destination_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for _skill_name, skill_path in resolved:
                    self._write_skill_archive_entries(archive, skill_path)
            message = f"Exported {len(resolved)} skill(s) to {destination_zip_path}"
            if skipped:
                message += f"\nSkipped non-exportable skills: {', '.join(skipped)}"
            return True, message
        except Exception as e:
            return False, f"Export failed: {e}"

    def delete_skill(self, skill_name):
        normalized = str(skill_name or "").strip()
        if not normalized:
            return {"status": "skipped", "skill_name": normalized, "message": "Skill name is required."}
        skill_path = self._find_user_skill_path(normalized)
        if not skill_path:
            return {
                "status": "skipped",
                "skill_name": normalized,
                "message": f"Skill '{normalized}' is not a user skill in ai_skills.",
            }
        try:
            shutil.rmtree(skill_path)
            return {"status": "deleted", "skill_name": normalized, "message": f"Skill '{normalized}' deleted."}
        except Exception as e:
            return {"status": "failed", "skill_name": normalized, "message": f"Failed to delete '{normalized}': {e}"}

    def delete_skill_collection(self, skill_names):
        summary = {"deleted": [], "skipped": [], "failed": []}
        seen = set()
        for skill_name in skill_names or []:
            normalized = str(skill_name or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result = self.delete_skill(normalized)
            summary.setdefault(result.get("status") or "failed", []).append(result)
        if summary["deleted"]:
            self.load_skills()
        lines = [
            f"删除完成：{len(summary['deleted'])} 个已删除，{len(summary['skipped'])} 个跳过，{len(summary['failed'])} 个失败。"
        ]
        if summary["deleted"]:
            lines.append("已删除：" + "、".join(item.get("skill_name") or "" for item in summary["deleted"][:8]))
        if summary["skipped"]:
            lines.append("已跳过：" + "、".join(item.get("skill_name") or "" for item in summary["skipped"][:8]))
        if summary["failed"]:
            lines.append("失败：" + "、".join(item.get("skill_name") or "" for item in summary["failed"][:8]))
        return {
            "ok": bool(summary["deleted"]) and not summary["failed"],
            "summary": summary,
            "message": "\n".join(lines),
        }

    def _write_skill_md(self, md_path, meta, body):
        preferred = [
            "name",
            "description",
            "description_cn",
            "license",
            "type",
            "created_by",
            "kind",
            "capability_group",
            "allowed-tools",
            "experience",
        ]
        ordered_keys = []
        for key in preferred:
            if key in meta:
                ordered_keys.append(key)
        for key in meta:
            if key not in ordered_keys:
                ordered_keys.append(key)
        lines = [f"{key}: {self._format_frontmatter_value(meta[key])}" for key in ordered_keys]
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"---\n{chr(10).join(lines)}\n---\n")
            if body:
                f.write("\n" + body.strip() + "\n")

    def _append_experience_entry(self, skill_path, spec, entry):
        entries_path = self._experience_entries_path(skill_path, spec=spec)
        os.makedirs(os.path.dirname(entries_path), exist_ok=True)
        with open(entries_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _ensure_summary_experience(self, skill_path, entry_text):
        md_path = os.path.join(skill_path, "SKILL.md")
        meta, body = self._parse_skill_md_content(md_path)
        current = meta.get("experience")
        if isinstance(current, str):
            current = [current]
        if not isinstance(current, list):
            current = []
        if entry_text not in current:
            current.append(entry_text)
        meta["experience"] = current
        self._write_skill_md(md_path, meta, body)

    def _ensure_general_experience_skill(self):
        for root in self.skills_dirs:
            candidate = os.path.join(root, "general-experience")
            if os.path.isdir(candidate):
                return candidate
        target_dir = os.path.join(self._default_writable_skill_root(), "general-experience")
        os.makedirs(target_dir, exist_ok=True)
        md_path = os.path.join(target_dir, "SKILL.md")
        skill_json_path = os.path.join(target_dir, "skill.json")
        if not os.path.exists(md_path):
            meta = {
                "name": "general-experience",
                "description": "General runtime experience package for cross-task lessons learned.",
                "license": "Apache-2.0",
                "type": "ai_generated",
                "created_by": "system",
                "kind": "knowledge",
                "capability_group": "memory-meta",
                "experience": [],
            }
            body = (
                "# Skill Purpose\nCapture cross-task runtime lessons that do not naturally belong to a narrower skill.\n\n"
                "## When to Use\nUse this experience package for execution patterns, environment caveats, recovery strategies, and collaboration rules.\n\n"
                "## When Not to Use\nDo not store highly specific tool guidance here when it clearly belongs to another skill.\n\n"
                "## Common Pitfalls\nAvoid turning one-off debugging notes into generic rules without evidence.\n\n"
                "## Experience / Lessons Learned\nAdd high-value cross-task lessons here.\n"
            )
            self._write_skill_md(md_path, meta, body)
        if not os.path.exists(skill_json_path):
            payload = {
                "version": 2,
                "name": "general-experience",
                "kind": "knowledge",
                "capability_group": "memory-meta",
                "description": "General runtime experience package for cross-task lessons learned.",
                "tags": ["experience", "runtime", "lessons"],
                "triggers": ["lesson learned", "execution pattern", "runtime issue"],
                "anti_triggers": [],
                "tool_refs": [],
                "references": [],
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
                    "Capture cross-task execution lessons as structured entries.",
                    "Promote high-value patterns into the summary when they recur.",
                ],
            }
            with open(skill_json_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        return target_dir

    def get_all_skills(self):
        self._scan_dist_dirs()
        all_skills = []
        seen = set()
        for skills_dir in self.skills_dirs:
            if not os.path.exists(skills_dir):
                continue
            is_ai_dir = os.path.basename(skills_dir) == "ai_skills"
            for skill_name in sorted(os.listdir(skills_dir)):
                if skill_name.startswith(".") or skill_name == "__pycache__" or skill_name in seen:
                    continue
                skill_path = os.path.join(skills_dir, skill_name)
                if not os.path.isdir(skill_path):
                    continue
                record = self.skill_records.get(skill_name) or self._load_skill_record(skill_name, skill_path)
                info = {
                    "name": skill_name,
                    "display_name": record["meta"].get("display_name") or _humanize_skill_name(skill_name),
                    "path": skill_path,
                    "description": record["spec"].get("description") or record["meta"].get("description") or "No description available.",
                    "user_description": record["meta"].get("description_cn") or record["spec"].get("description_cn") or record["spec"].get("description") or record["meta"].get("description") or "暂无说明。",
                    "enabled": True,
                    "tools": list(record.get("tool_refs") or []),
                    "kind": record["kind"],
                    "capability_group": record["spec"].get("capability_group"),
                    "experience_count": len(record.get("experience_entries") or []),
                    "use_cases": record["spec"].get("triggers") or record["spec"].get("tags") or [],
                    "risk_level": record["meta"].get("security_level") or record["spec"].get("security_level") or "medium",
                    "dependency_status": record.get("dependency_status") or {"ok": True},
                    "script_refs": list(record["spec"].get("script_refs") or []),
                    "script_entries": list(record["spec"].get("script_entries") or []),
                    "source_format": record["spec"].get("source_format"),
                }
                if is_ai_dir:
                    info["type"] = "ai_generated"
                    info["created_by"] = "ai"
                if self.config_manager:
                    info["enabled"] = self.config_manager.is_skill_enabled(skill_name)
                info.update({k: v for k, v in record["meta"].items() if k != "allowed-tools"})
                info.update({k: v for k, v in record["spec"].items() if k not in {"workflow", "tool_refs", "experience_policy", "disclosure_level_defaults"}})
                all_skills.append(info)
                seen.add(skill_name)
        for skill_name, record in self.skill_records.items():
            if skill_name in seen:
                continue
            if record.get("spec", {}).get("source_format") != self.MCP_SOURCE_FORMAT:
                continue
            info = {
                "name": skill_name,
                "display_name": record["meta"].get("display_name") or _humanize_skill_name(skill_name),
                "path": record.get("path") or "",
                "description": record["spec"].get("description") or record["meta"].get("description") or "No description available.",
                "user_description": record["meta"].get("description_cn") or record["spec"].get("description_cn") or record["spec"].get("description") or record["meta"].get("description") or "暂无说明。",
                "enabled": True,
                "tools": list(record.get("tool_refs") or []),
                "kind": record["kind"],
                "capability_group": record["spec"].get("capability_group"),
                "experience_count": len(record.get("experience_entries") or []),
                "use_cases": record["spec"].get("triggers") or record["spec"].get("tags") or [],
                "risk_level": record["meta"].get("security_level") or record["spec"].get("security_level") or "medium",
                "dependency_status": record.get("dependency_status") or {"ok": True},
                "script_refs": list(record["spec"].get("script_refs") or []),
                "script_entries": list(record["spec"].get("script_entries") or []),
                "source_format": record["spec"].get("source_format"),
            }
            if self.config_manager:
                info["enabled"] = self.config_manager.is_skill_enabled(skill_name)
            info.update({k: v for k, v in record["meta"].items() if k != "allowed-tools"})
            info.update({k: v for k, v in record["spec"].items() if k not in {"workflow", "tool_refs", "experience_policy", "disclosure_level_defaults"}})
            all_skills.append(info)
            seen.add(skill_name)
        return all_skills

    def import_skill(self, source_path, source_format="auto"):
        temp_dir = None
        resolved_source_path = source_path
        if os.path.isfile(source_path):
            if os.path.splitext(source_path)[1].lower() != ".zip":
                return False, "Source must be a directory or ZIP file"
            try:
                temp_dir = self._extract_zip_to_tempdir(source_path)
                resolved_source_path = self._resolve_import_source_dir(temp_dir)
            except zipfile.BadZipFile:
                return False, "Import failed: ZIP file is invalid."
            except Exception as e:
                return False, f"Import failed: {e}"
        elif not os.path.isdir(source_path):
            return False, "Source must be a directory or ZIP file"
        try:
            candidate_dirs = discover_importable_skill_dirs(resolved_source_path)
            if not candidate_dirs:
                return False, "Import failed: no importable skill directories were found."
            if len(candidate_dirs) == 1:
                result = self._import_single_skill_dir(candidate_dirs[0], source_format=source_format)
                if result.get("status") == "skipped_existing":
                    return False, result.get("message") or "Skill already exists"
                return True, result.get("message") or f"Skill '{result.get('skill_name')}' imported successfully"

            summary = {"imported": [], "skipped_existing": [], "failed": []}
            for candidate_dir in candidate_dirs:
                try:
                    result = self._import_single_skill_dir(candidate_dir, source_format=source_format)
                except Exception as e:
                    result = {
                        "status": "failed",
                        "skill_name": self._read_skill_name_from_path(candidate_dir),
                        "message": f"Import failed: {e}",
                    }
                summary[result.get("status") or "failed"].append(result)
            success = bool(summary["imported"]) and not summary["failed"]
            if summary["imported"] and not success:
                success = True
            if not summary["imported"] and not summary["skipped_existing"]:
                success = False
            return success, self._format_import_summary_message(summary)
        except Exception as e:
            return False, f"Import failed: {e}"
        finally:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)

    def update_skill(self, skill_name, description=None, instructions=None, experience=None, replace_experience=False):
        skill_path = None
        for s_dir in self.skills_dirs:
            candidate = os.path.join(s_dir, skill_name)
            if os.path.isdir(candidate):
                skill_path = candidate
                break
        if not skill_path:
            return False, f"Skill '{skill_name}' not found."
        md_path = os.path.join(skill_path, "SKILL.md")
        if not os.path.exists(md_path):
            return False, f"SKILL.md not found for '{skill_name}'."
        try:
            meta, body = self._parse_skill_md_content(md_path)
            spec = self._load_skill_json(skill_path)
            if description:
                meta["description"] = description
                spec["description"] = description
            if instructions is not None:
                body = instructions
            if experience:
                current = meta.get("experience")
                if isinstance(current, str):
                    current = [current]
                if not isinstance(current, list):
                    current = []
                additions = experience if isinstance(experience, list) else [experience]
                if replace_experience:
                    current = [item for item in additions if item]
                else:
                    for item in additions:
                        if item and item not in current:
                            current.append(item)
                            entry = {
                                "id": str(uuid.uuid4()),
                                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                "source": "manual_update",
                                "experience_text": item,
                            }
                            self._append_experience_entry(skill_path, spec, entry)
                meta["experience"] = current
            self._write_skill_md(md_path, meta, body)
            if spec:
                spec.setdefault("version", 2)
                spec.setdefault("name", skill_name)
                with open(os.path.join(skill_path, "skill.json"), "w", encoding="utf-8") as f:
                    json.dump(spec, f, ensure_ascii=False, indent=2)
                dependency_status = self._prepare_skill_dependencies(skill_name, skill_path)
                if not dependency_status.get("ok"):
                    return True, f"Skill '{skill_name}' updated, but dependency setup is incomplete: {dependency_status.get('message')}"
            return True, f"Skill '{skill_name}' updated successfully."
        except Exception as e:
            return False, f"Failed to update skill: {e}"

    def record_experience(
        self,
        experience_text,
        skill_name=None,
        tool_name=None,
        workspace_hint=None,
        task_type=None,
        error_pattern=None,
        importance="medium",
        tags=None,
        source="execution_feedback",
    ):
        if not experience_text:
            return False, "experience_text is required."

        target_skill = skill_name
        if not target_skill:
            general_path = self._ensure_general_experience_skill()
            target_skill = os.path.basename(general_path)

        skill_record = self.skill_records.get(target_skill)
        if not skill_record:
            self.load_skills()
            skill_record = self.skill_records.get(target_skill)
        if not skill_record:
            return False, f"Skill '{target_skill}' not found."

        entry = {
            "id": str(uuid.uuid4()),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": source,
            "experience_text": experience_text,
            "tool_name": tool_name,
            "workspace_hint": workspace_hint or self.workspace_dir,
            "task_type": task_type,
            "error_pattern": error_pattern,
            "importance": importance,
            "tags": _json_copy(tags, []) if isinstance(tags, list) else [],
        }
        try:
            self._append_experience_entry(skill_record["path"], skill_record["spec"], entry)
            self._ensure_summary_experience(skill_record["path"], experience_text)
            self.load_skills()
            return True, f"Recorded experience in '{target_skill}'."
        except Exception as e:
            return False, f"Failed to record experience: {e}"

    def update_skill_experience(self, skill_name, experience_text):
        return self.record_experience(experience_text=experience_text, skill_name=skill_name)

    def get_experience_entries(self, skill_name):
        record = self.skill_records.get(skill_name)
        if not record:
            return []
        return list(record.get("experience_entries") or [])

    def check_for_updates(self):
        try:
            for _, record in self.skill_records.items():
                base_path = record["path"]
                for filename in ("SKILL.md", "skill.json", "impl.py"):
                    candidate = os.path.join(base_path, filename)
                    if os.path.exists(candidate) and os.path.getmtime(candidate) > self.last_load_time:
                        return True
                entries_path = self._experience_entries_path(base_path, spec=record.get("spec") or {})
                if os.path.isfile(entries_path) and os.path.getmtime(entries_path) > self.last_load_time:
                    return True
                for ref in record.get("spec", {}).get("references") or []:
                    candidate = os.path.join(base_path, ref)
                    if os.path.isfile(candidate) and os.path.getmtime(candidate) > self.last_load_time:
                        return True
        except Exception as e:
            print(f"Error checking for updates: {e}")
        return False

    def load_skills(self):
        if not hasattr(self, "tool_registry"):
            self.tool_registry = ToolRegistry()
        if not hasattr(self, "workspace_dir"):
            self.workspace_dir = None
        if not hasattr(self, "config_manager"):
            self.config_manager = None
        self.tools = {}
        self.tool_definitions = []
        self.tool_registry.clear()
        self.tool_to_skill_map = {}
        self.tool_records = {}
        self.skill_to_tools = {}
        self.loaded_skills_meta = {}
        self.loaded_skill_sources = {}
        self.skill_prompts_brief = []
        self.skill_prompts_full = {}
        self.skill_records = {}
        self.experience_packages = self.skill_records
        self.last_load_time = time.time()
        self._register_builtin_tools()

        seen = set()
        pending_records = []
        for skills_dir in self.skills_dirs:
            if not os.path.exists(skills_dir):
                continue
            for skill_name in sorted(os.listdir(skills_dir)):
                if skill_name.startswith(".") or skill_name == "__pycache__" or skill_name in seen:
                    continue
                if self.config_manager and not self.config_manager.is_skill_enabled(skill_name):
                    continue
                skill_path = os.path.join(skills_dir, skill_name)
                if not os.path.isdir(skill_path):
                    continue
                seen.add(skill_name)
                self.loaded_skill_sources[skill_name] = skill_path
                dependency_status = self._prepare_skill_dependencies(skill_name, skill_path)
                impl_path = os.path.join(skill_path, "impl.py")
                if os.path.exists(impl_path):
                    self._load_legacy_implementation(skill_name, impl_path)
                pending_records.append((skill_name, skill_path, dependency_status))

        for skill_name, skill_path, dependency_status in pending_records:
            try:
                record = self._load_skill_record(skill_name, skill_path)
            except Exception as e:
                print(f"[SkillManager] Failed to load skill '{skill_name}' from {skill_path}: {e}")
                continue
            registered_tools = list(self.skill_to_tools.get(skill_name) or [])
            if registered_tools:
                merged_tool_refs = []
                for tool_ref in list(record.get("tool_refs") or []) + registered_tools:
                    if tool_ref not in merged_tool_refs:
                        merged_tool_refs.append(tool_ref)
                record["tool_refs"] = merged_tool_refs
                record["spec"]["tool_refs"] = list(merged_tool_refs)
            record["brief"] = self._build_brief_prompt(skill_name, record["meta"], record["spec"], record["tool_refs"])
            record["prompt"] = self._build_skill_prompt(
                skill_name,
                record["meta"],
                record["body"],
                record["spec"],
                record["tool_refs"],
                experience_entries=[],
                include_references=False,
            )
            record["search_text"] = "\n".join(
                [
                    record.get("search_text", ""),
                    self._summarize_experience_entries(record.get("experience_entries") or [], limit=10),
                ]
            ).strip()
            self.loaded_skills_meta[skill_name] = record["meta"]
            record["dependency_status"] = dependency_status
            self.skill_prompts_brief.append(record["brief"])
            self.skill_prompts_full[skill_name] = record["prompt"]
            self.skill_records[skill_name] = record
            self.skill_to_tools.setdefault(skill_name, list(record.get("tool_refs") or []))
        self._load_mcp_servers()

    def _explicit_skill_matches(self, query_tokens):
        matches = []
        for skill_name in self.skill_records:
            name_tokens = _tokenize(skill_name.replace("-", " "))
            if query_tokens & name_tokens:
                matches.append(skill_name)
        return matches

    def select_relevant_skills(self, query_text, limit=5):
        query_tokens = _tokenize(query_text)
        normalized_query = _normalize_search_text(query_text)
        if not query_tokens and not normalized_query:
            return []
        explicit = set(self._explicit_skill_matches(query_tokens))
        ranked = []
        for skill_name, record in self.skill_records.items():
            spec = record["spec"]
            score = 100 if skill_name in explicit else 0
            tags = _tokenize(" ".join(spec.get("tags") or []))
            triggers = _tokenize(" ".join(spec.get("triggers") or []))
            anti = _tokenize(" ".join(spec.get("anti_triggers") or []))
            search_tokens = _tokenize(record["search_text"])
            score += len(query_tokens & tags) * 15
            score += len(query_tokens & triggers) * 12
            score += len(query_tokens & search_tokens) * 3
            score -= len(query_tokens & anti) * 20
            normalized_search = _normalize_search_text(record["search_text"])
            if normalized_query and normalized_search and normalized_query in normalized_search:
                score += 10
            priority = spec.get("priority", 0)
            if isinstance(priority, int):
                score += priority
            if score > 0:
                ranked.append((score, skill_name))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [name for _, name in ranked[:limit]]

    def get_skill_of_tool(self, tool_name):
        return self.tool_to_skill_map.get(tool_name)

    def get_tool_definitions(self, run_mode=None, discovered_tool_names=None, include_deferred=None, run_context=None):
        if run_mode is None and discovered_tool_names is None and include_deferred is None and run_context is None:
            return self.tool_definitions
        definitions = self.tool_registry.definitions(
            run_mode=run_mode,
            discovered_tool_names=discovered_tool_names,
            include_deferred=bool(include_deferred),
        )
        definitions = self._filter_definitions_by_allowed_skills(definitions, run_context)
        definitions = self._filter_enterprise_tool_definitions(definitions, run_context)
        if self._is_enterprise_tool_allowed("publish_artifacts", run_context):
            publish_definition = self._get_tool_definition("publish_artifacts")
            if publish_definition and not any(
                (item.get("function") or {}).get("name") == "publish_artifacts"
                for item in definitions
                if isinstance(item, dict)
            ):
                definitions.append(publish_definition)
        return definitions

    def _get_tool_definition(self, name):
        record = self.tool_registry.get(name)
        if not record:
            return None
        return record.to_definition()

    def _normalize_run_context_for_tools(self, run_context):
        return run_context if isinstance(run_context, dict) else {}

    def _is_enterprise_im_run_context(self, run_context):
        ctx = self._normalize_run_context_for_tools(run_context)
        enterprise_channels = {"feishu", "dingtalk", "wecom"}
        return (ctx.get("im_provider") or "").strip().lower() in enterprise_channels or (
            (ctx.get("channel") or "").strip().lower() in enterprise_channels
        )

    def _is_enterprise_tool_allowed(self, name, run_context):
        if name != "publish_artifacts":
            return True
        return self._is_enterprise_im_run_context(run_context)

    def _filter_enterprise_tool_results(self, results, run_context):
        filtered = []
        for item in results or []:
            if self._is_enterprise_tool_allowed(item.get("name"), run_context):
                filtered.append(item)
        return filtered

    def _filter_enterprise_tool_definitions(self, definitions, run_context):
        filtered = []
        for item in definitions or []:
            function = item.get("function") if isinstance(item, dict) else None
            name = function.get("name") if isinstance(function, dict) else ""
            if self._is_enterprise_tool_allowed(name, run_context):
                filtered.append(item)
        return filtered

    def _allowed_skill_names(self, run_context):
        ctx = self._normalize_run_context_for_tools(run_context)
        allowed = normalize_selected_skill_names(ctx.get("allowed_skill_names"))
        return [name for name in allowed if name in self.skill_records]

    def _is_skill_allowed_by_scope(self, skill_name, run_context):
        allowed_skill_names = self._allowed_skill_names(run_context)
        if not allowed_skill_names:
            return True
        return str(skill_name or "").strip() in allowed_skill_names

    def _is_tool_allowed_by_skill_scope(self, tool_name, run_context):
        allowed_skill_names = self._allowed_skill_names(run_context)
        if not allowed_skill_names:
            return True
        resolved_name = self.tool_registry.resolve_name(tool_name) or str(tool_name or "").strip()
        if resolved_name in self.ALWAYS_ALLOWED_SCOPE_TOOLS:
            return True
        skill_name = self.tool_to_skill_map.get(resolved_name)
        if not skill_name:
            return False
        return skill_name in allowed_skill_names

    def _filter_definitions_by_allowed_skills(self, definitions, run_context):
        filtered = []
        for item in definitions or []:
            function = item.get("function") if isinstance(item, dict) else None
            name = function.get("name") if isinstance(function, dict) else ""
            if self._is_tool_allowed_by_skill_scope(name, run_context):
                filtered.append(item)
        return filtered

    def _filter_results_by_allowed_skills(self, results, run_context):
        filtered = []
        for item in results or []:
            if self._is_tool_allowed_by_skill_scope(item.get("name"), run_context):
                filtered.append(item)
        return filtered

    def validate_tool_run_access(
        self,
        tool_name,
        *,
        run_context=None,
        discovered_tool_names=None,
        require_read_only=False,
        deny_tool_names=None,
    ):
        record = self.tool_registry.get(tool_name)
        resolved_name = record.name if record else str(tool_name or "").strip()
        if not resolved_name or resolved_name not in self.tools:
            return {"ok": False, "name": resolved_name, "record": record, "error": f"Tool '{tool_name}' not found."}
        if not record:
            return {"ok": False, "name": resolved_name, "record": record, "error": f"Tool '{resolved_name}' is not registered."}
        blocked = {str(item or "").strip() for item in (deny_tool_names or []) if str(item or "").strip()}
        if resolved_name in blocked:
            return {"ok": False, "name": resolved_name, "record": record, "error": f"Tool '{resolved_name}' is not allowed in this context."}
        if require_read_only and (not record.read_only or record.destructive):
            return {"ok": False, "name": resolved_name, "record": record, "error": f"Tool '{resolved_name}' is not a read-only tool."}
        if not self._is_tool_allowed_by_skill_scope(resolved_name, run_context):
            return {"ok": False, "name": resolved_name, "record": record, "error": f"Tool '{resolved_name}' is not allowed for this agent profile."}
        if not self._is_enterprise_tool_allowed(resolved_name, run_context):
            return {"ok": False, "name": resolved_name, "record": record, "error": f"Tool '{resolved_name}' is not available in this run context."}
        run_mode = (run_context or {}).get("mode")
        if not self.tool_registry.is_allowed(resolved_name, run_mode):
            return {"ok": False, "name": resolved_name, "record": record, "error": f"Tool '{resolved_name}' is not allowed in the current mode."}
        if not self.tool_registry.is_visible(resolved_name, run_mode, discovered_tool_names):
            return {"ok": False, "name": resolved_name, "record": record, "error": f"Tool '{resolved_name}' has not been discovered for this run."}
        return {"ok": True, "name": resolved_name, "record": record, "error": ""}

    def get_tools_for_skill(self, skill_name):
        return list(self.skill_to_tools.get(skill_name) or [])

    def get_skill_display_name(self, skill_name):
        record = self.skill_records.get(skill_name)
        if not record:
            return str(skill_name or "").strip()
        return record["meta"].get("display_name") or _humanize_skill_name(skill_name)

    def get_brief_skill_prompt(self, skill_name):
        record = self.skill_records.get(skill_name) or {}
        return record.get("brief") or ""

    def materialize_experience_package(self, skill_name, include_references=False, include_entries=False):
        record = self.skill_records.get(skill_name)
        if not record:
            return ""
        entries = record.get("experience_entries") or []
        return self._build_skill_prompt(
            skill_name,
            record["meta"],
            record["body"],
            record["spec"],
            record["tool_refs"],
            experience_entries=entries if include_entries else [],
            include_references=include_references,
        )

    def get_system_prompts(
        self,
        query_text=None,
        limit=6,
        preferred_skill_names=None,
        exclude_skill_names=None,
        allowed_skill_names=None,
    ):
        selected = []
        allowed_scope = set(normalize_selected_skill_names(allowed_skill_names))
        for skill_name in preferred_skill_names or []:
            if (
                skill_name in self.skill_records
                and skill_name not in selected
                and (not allowed_scope or skill_name in allowed_scope)
            ):
                selected.append(skill_name)
        if query_text:
            for skill_name in self.select_relevant_skills(query_text, limit=limit):
                if skill_name not in selected and (not allowed_scope or skill_name in allowed_scope):
                    selected.append(skill_name)
        elif not selected:
            selected = [
                name for name in self.skill_records
                if not allowed_scope or name in allowed_scope
            ][:limit]
        excluded = {name for name in (exclude_skill_names or []) if name}
        blocks = []
        for skill_name in selected:
            if skill_name in excluded:
                continue
            record = self.skill_records.get(skill_name) or {}
            blocks.append(record.get("brief") or "")
            if len(blocks) >= limit:
                break
        return "\n\n".join([block for block in blocks if block])

    def get_full_disclosure_skill_names(
        self,
        query_text=None,
        limit=6,
        preferred_skill_names=None,
        exclude_skill_names=None,
        allowed_skill_names=None,
    ):
        selected = []
        allowed_scope = set(normalize_selected_skill_names(allowed_skill_names))
        for skill_name in preferred_skill_names or []:
            if (
                skill_name in self.skill_records
                and skill_name not in selected
                and (not allowed_scope or skill_name in allowed_scope)
            ):
                selected.append(skill_name)
        if query_text:
            for skill_name in self.select_relevant_skills(query_text, limit=limit):
                if skill_name not in selected and (not allowed_scope or skill_name in allowed_scope):
                    selected.append(skill_name)
        excluded = {name for name in (exclude_skill_names or []) if name}
        matched = []
        for skill_name in selected:
            if skill_name in excluded:
                continue
            record = self.skill_records.get(skill_name) or {}
            spec = record.get("spec") or {}
            if (spec.get("prompt_disclosure") or self._infer_prompt_disclosure(spec)) != "full_on_match":
                continue
            matched.append(skill_name)
            if len(matched) >= limit:
                break
        return matched

    def get_full_skill_prompt(self, skill_name, include_references=False, include_entries=False):
        if not include_references and not include_entries:
            return self.skill_prompts_full.get(skill_name)
        return self.materialize_experience_package(skill_name, include_references=include_references, include_entries=include_entries)

    def call_tool(self, name, args, context=None):
        record = self.tool_registry.get(name)
        resolved_name = record.name if record else str(name or "").strip()
        if resolved_name not in self.tools:
            return f"Error: Tool '{name}' not found."
        effective_context = dict(context or {}) if isinstance(context, dict) else {}
        effective_context.setdefault("skill_manager", self)
        run_context = effective_context.get("run_context") if isinstance(effective_context, dict) else None
        if not self._is_tool_allowed_by_skill_scope(resolved_name, run_context):
            return f"Error: Tool '{name}' is not allowed for this agent profile."
        skill_name = self.tool_to_skill_map.get(resolved_name)
        if skill_name:
            record = self.skill_records.get(skill_name)
            dependency_status = (record or {}).get("dependency_status") or {"ok": True}
            if not dependency_status.get("ok") and record:
                dependency_status = self._prepare_skill_dependencies(skill_name, record["path"])
                record["dependency_status"] = dependency_status
                if not dependency_status.get("ok"):
                    return f"Error: Dependencies for skill '{skill_name}' are not ready: {dependency_status.get('message')}"
        func = self.tools[resolved_name]
        sig = inspect.signature(func)
        args = dict(args or {})
        if "workspace_dir" in sig.parameters:
            args["workspace_dir"] = self.workspace_dir
        if "_context" in sig.parameters:
            args["_context"] = effective_context
        try:
            return func(**args)
        except Exception as e:
            return f"Error executing {name}: {str(e)}"
