import importlib.util
import inspect
import json
import os
import re
import shutil
import sys
import time
import uuid

from .env_utils import ensure_package_installed, get_app_data_dir


def _tokenize(text):
    return set(re.findall(r"[a-z0-9][a-z0-9_\-]+", (text or "").lower()))


def _json_copy(value, fallback):
    if value is None:
        return fallback
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except Exception:
        return fallback


class SkillManager:
    GROUP_DEFAULTS = {
        "file-system": "file-information-interaction",
        "web-search": "file-information-interaction",
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
            dist_dir = os.path.join(repo_root, "dist")
            if os.path.exists(dist_dir):
                for item in os.listdir(dist_dir):
                    for folder in ("skills", "ai_skills"):
                        candidate = os.path.join(dist_dir, item, folder)
                        if os.path.isdir(candidate):
                            self.skills_dirs.append(candidate)

        self.tools = {}
        self.tool_definitions = []
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

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        dist_dir = os.path.join(repo_root, "dist")
        if not os.path.exists(dist_dir):
            return
        for item in os.listdir(dist_dir):
            for folder in ("skills", "ai_skills"):
                candidate = os.path.join(dist_dir, item, folder)
                if os.path.isdir(candidate) and candidate not in self.skills_dirs:
                    self.skills_dirs.append(candidate)

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

    def _normalize_skill_kind(self, meta, spec):
        kind = (spec.get("kind") or meta.get("kind") or "").strip().lower()
        if kind in {"knowledge", "system"}:
            return kind
        return "knowledge"

    def _infer_capability_group(self, skill_name, meta, spec):
        return (
            spec.get("capability_group")
            or meta.get("capability_group")
            or self.GROUP_DEFAULTS.get(skill_name, "knowledge")
        )

    def _normalize_disclosure_defaults(self, spec):
        defaults = spec.get("disclosure_level_defaults")
        if isinstance(defaults, dict):
            return defaults
        return {
            "default_prompt_level": "brief",
            "include_references": False,
            "include_experience_entries": False,
        }

    def _normalize_experience_policy(self, spec):
        policy = spec.get("experience_policy")
        if isinstance(policy, dict):
            return policy
        return {
            "entry_storage": "experience/entries.jsonl",
            "summary_sync": "frontmatter_experience",
        }

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
        allowed = meta.get("allowed-tools")
        if isinstance(allowed, list):
            refs.extend([item for item in allowed if isinstance(item, str) and item])
        elif isinstance(allowed, str) and allowed:
            refs.append(allowed)
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

        self.tools[tool_name] = func
        self.tool_to_skill_map[tool_name] = skill_name
        self.skill_to_tools.setdefault(skill_name, []).append(tool_name)

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
        self.tool_definitions.append(
            {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": tool_description,
                    "parameters": parameters_schema,
                },
            }
        )
        self.tool_records[tool_name] = {
            "name": tool_name,
            "description": tool_description,
            "kind": tool_kind,
            "parameters_schema": parameters_schema,
            "runtime_binding": {"type": "python_function", "skill_name": skill_name},
            "skill_refs": [skill_name],
        }

    def _load_legacy_implementation(self, skill_name, impl_path):
        try:
            spec = importlib.util.spec_from_file_location(f"skills.{skill_name}", impl_path)
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except ImportError as e:
                missing_pkg = getattr(e, "name", None)
                if missing_pkg:
                    ensure_package_installed(missing_pkg)
                    spec.loader.exec_module(module)
                else:
                    raise e
            for name, func in inspect.getmembers(module, inspect.isfunction):
                if name.startswith("_"):
                    continue
                if getattr(func, "__module__", None) != getattr(module, "__name__", None):
                    continue
                tool_kind = "system_entry" if skill_name in self.SYSTEM_SKILLS else "legacy_function"
                self._register_tool(skill_name, name, func, tool_kind=tool_kind)
        except Exception as e:
            print(f"Error loading implementation {impl_path}: {e}")

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

    def _load_skill_record(self, skill_name, skill_path):
        md_path = os.path.join(skill_path, "SKILL.md")
        meta, body = self._parse_skill_md_content(md_path) if os.path.exists(md_path) else ({}, "")
        spec = self._load_skill_json(skill_path)
        tool_refs = self._parse_tool_refs(meta, spec)
        kind = self._normalize_skill_kind(meta, spec)
        if not meta and not spec:
            return self._build_minimal_skill_record(skill_name, skill_path, tool_refs)
        spec.setdefault("version", 2)
        spec.setdefault("name", meta.get("name") or skill_name)
        spec.setdefault("kind", kind)
        spec.setdefault("description", spec.get("description") or meta.get("description") or "")
        spec.setdefault("capability_group", self._infer_capability_group(skill_name, meta, spec))
        spec.setdefault("tool_refs", tool_refs)
        spec.setdefault("experience_policy", self._normalize_experience_policy(spec))
        spec.setdefault("disclosure_level_defaults", self._normalize_disclosure_defaults(spec))
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
                meta.get("description", ""),
                spec.get("description", ""),
                body,
                " ".join(spec.get("tags") or []),
                " ".join(spec.get("triggers") or []),
                " ".join(tool_refs),
                self._summarize_experience_entries(experience_entries, limit=10),
            ]
        )
        return record

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
            for skill_name in os.listdir(skills_dir):
                if skill_name.startswith(".") or skill_name == "__pycache__" or skill_name in seen:
                    continue
                skill_path = os.path.join(skills_dir, skill_name)
                if not os.path.isdir(skill_path):
                    continue
                record = self.skill_records.get(skill_name) or self._load_skill_record(skill_name, skill_path)
                info = {
                    "name": skill_name,
                    "path": skill_path,
                    "description": record["spec"].get("description") or record["meta"].get("description") or "No description available.",
                    "enabled": True,
                    "tools": list(record.get("tool_refs") or []),
                    "kind": record["kind"],
                    "capability_group": record["spec"].get("capability_group"),
                    "experience_count": len(record.get("experience_entries") or []),
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
        return all_skills

    def import_skill(self, source_path):
        if not os.path.isdir(source_path):
            return False, "Source is not a directory"
        skill_name = os.path.basename(source_path)
        if not os.path.exists(os.path.join(source_path, "SKILL.md")):
            return False, "SKILL.md not found in source directory"
        target_dir = self._default_writable_skill_root()
        target_path = os.path.join(target_dir, skill_name)
        if os.path.exists(target_path):
            return False, f"Skill '{skill_name}' already exists"
        try:
            shutil.copytree(source_path, target_path)
            return True, f"Skill '{skill_name}' imported successfully"
        except Exception as e:
            return False, f"Import failed: {e}"

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
        self.tools = {}
        self.tool_definitions = []
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

        seen = set()
        pending_records = []
        for skills_dir in self.skills_dirs:
            if not os.path.exists(skills_dir):
                continue
            for skill_name in os.listdir(skills_dir):
                if skill_name.startswith(".") or skill_name == "__pycache__" or skill_name in seen:
                    continue
                if self.config_manager and not self.config_manager.is_skill_enabled(skill_name):
                    continue
                skill_path = os.path.join(skills_dir, skill_name)
                if not os.path.isdir(skill_path):
                    continue
                seen.add(skill_name)
                self.loaded_skill_sources[skill_name] = skill_path
                impl_path = os.path.join(skill_path, "impl.py")
                if os.path.exists(impl_path):
                    self._load_legacy_implementation(skill_name, impl_path)
                pending_records.append((skill_name, skill_path))

        for skill_name, skill_path in pending_records:
            record = self._load_skill_record(skill_name, skill_path)
            if not record.get("tool_refs") and self.skill_to_tools.get(skill_name):
                record["tool_refs"] = list(self.skill_to_tools.get(skill_name) or [])
                record["spec"]["tool_refs"] = list(record["tool_refs"])
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
            self.skill_prompts_brief.append(record["brief"])
            self.skill_prompts_full[skill_name] = record["prompt"]
            self.skill_records[skill_name] = record
            self.skill_to_tools.setdefault(skill_name, list(record.get("tool_refs") or []))

    def _explicit_skill_matches(self, query_tokens):
        matches = []
        for skill_name in self.skill_records:
            name_tokens = _tokenize(skill_name.replace("-", " "))
            if query_tokens & name_tokens:
                matches.append(skill_name)
        return matches

    def select_relevant_skills(self, query_text, limit=5):
        query_tokens = _tokenize(query_text)
        if not query_tokens:
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
            priority = spec.get("priority", 0)
            if isinstance(priority, int):
                score += priority
            if score > 0:
                ranked.append((score, skill_name))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [name for _, name in ranked[:limit]]

    def get_skill_of_tool(self, tool_name):
        return self.tool_to_skill_map.get(tool_name)

    def get_tool_definitions(self):
        return self.tool_definitions

    def get_tools_for_skill(self, skill_name):
        return list(self.skill_to_tools.get(skill_name) or [])

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

    def get_system_prompts(self, query_text=None, limit=6):
        if not query_text:
            return "\n\n".join(self.skill_prompts_brief[:limit])
        selected = self.select_relevant_skills(query_text, limit=limit)
        blocks = []
        for skill_name in selected:
            record = self.skill_records.get(skill_name) or {}
            blocks.append(record.get("brief") or "")
        return "\n\n".join([block for block in blocks if block])

    def get_full_skill_prompt(self, skill_name, include_references=False, include_entries=False):
        if not include_references and not include_entries:
            return self.skill_prompts_full.get(skill_name)
        return self.materialize_experience_package(skill_name, include_references=include_references, include_entries=include_entries)

    def call_tool(self, name, args, context=None):
        if name not in self.tools:
            return f"Error: Tool '{name}' not found."
        func = self.tools[name]
        sig = inspect.signature(func)
        if "workspace_dir" in sig.parameters:
            args["workspace_dir"] = self.workspace_dir
        if context and "_context" in sig.parameters:
            args["_context"] = context
        try:
            return func(**args)
        except Exception as e:
            return f"Error executing {name}: {str(e)}"
