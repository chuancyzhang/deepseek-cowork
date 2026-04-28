import copy
import math
import re
from dataclasses import dataclass, field

from .plan_mode import (
    PLANNING_ALLOWED_TOOLS,
    RUN_MODE_EXECUTION,
    RUN_MODE_NORMAL,
    RUN_MODE_PLANNING,
    normalize_run_mode,
)


CORE_ALWAYS_LOAD_TOOLS = {
    "tool_search",
    "request_user_input",
    "request_user_approval",
    "read_memories",
    "update_experience",
}

READ_ONLY_TOOL_NAMES = {
    "list_files",
    "read_file",
    "glob",
    "grep",
    "read_docx",
    "read_pptx",
    "read_excel",
    "read_pdf",
    "search_files",
    "search_codebase",
    "query_history",
    "query_history_vector",
    "read_memories",
    "search_web",
    "read_web_article",
    "analyze_skill_source_folder",
    "list_agents",
}

DESTRUCTIVE_PREFIXES = (
    "write_",
    "update_",
    "delete_",
    "rename_",
    "create_",
    "install_",
    "run_",
    "launch_",
    "open_",
    "publish_",
    "spawn_",
    "send_",
    "close_",
)

EXECUTION_ONLY_TOOL_NAMES = {
    "bash",
    "run_python_code",
    "run_skill_script",
    "system_automate",
    "build_app_index",
    "find_app",
    "launch_app",
    "open_with",
    "write_file",
    "update_file",
    "rename_file",
    "delete_file",
    "write_docx",
    "create_pptx",
    "write_excel",
    "write_memories",
    "create_new_skill",
    "update_skill",
    "convert_claude_skill",
    "convert_openclaw_skill",
    "convert_external_skill",
    "generate_skill_from_folder",
    "install_package",
    "spawn_agent",
    "send_input",
    "wait_agent",
    "close_agent",
    "publish_artifacts",
}


def _tokenize(text):
    return re.findall(r"[a-z0-9][a-z0-9_\-]*", str(text or "").lower())


def _as_string_list(value):
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


@dataclass
class ToolRecord:
    name: str
    description: str
    parameters_schema: dict
    handler: object
    skill_name: str = ""
    kind: str = "tool"
    aliases: list = field(default_factory=list)
    search_hint: str = ""
    read_only: bool = False
    destructive: bool = False
    allowed_modes: set = field(default_factory=lambda: {RUN_MODE_EXECUTION})
    should_defer: bool = True
    always_load: bool = False
    runtime_binding: dict = field(default_factory=dict)
    skill_refs: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def to_definition(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": copy.deepcopy(self.parameters_schema),
            },
        }

    def search_text(self):
        parts = [
            self.name.replace("_", " "),
            " ".join(self.aliases),
            self.search_hint,
            self.description,
            self.skill_name,
            self.kind,
        ]
        return "\n".join([part for part in parts if part])


class ToolRegistry:
    def __init__(self):
        self.records = {}
        self.alias_to_name = {}

    def clear(self):
        self.records = {}
        self.alias_to_name = {}

    def register(
        self,
        name,
        handler,
        description,
        parameters_schema,
        *,
        skill_name="",
        kind="tool",
        aliases=None,
        search_hint="",
        read_only=None,
        destructive=None,
        allowed_modes=None,
        should_defer=None,
        always_load=None,
        runtime_binding=None,
        skill_refs=None,
        metadata=None,
    ):
        name = str(name or "").strip()
        if not name or not callable(handler) or name in self.records or name in self.alias_to_name:
            return None

        aliases = _as_string_list(aliases)
        for alias in aliases:
            if alias in self.records or alias in self.alias_to_name:
                return None

        inferred_read_only = self._infer_read_only(name) if read_only is None else bool(read_only)
        inferred_destructive = self._infer_destructive(name) if destructive is None else bool(destructive)
        modes = self._normalize_allowed_modes(
            allowed_modes,
            name=name,
            read_only=inferred_read_only,
        )
        load_always = self._infer_always_load(name) if always_load is None else bool(always_load)
        defer = (not load_always) if should_defer is None else bool(should_defer)

        record = ToolRecord(
            name=name,
            description=str(description or f"Tool {name}").strip(),
            parameters_schema=copy.deepcopy(parameters_schema or {"type": "object", "properties": {}, "required": []}),
            handler=handler,
            skill_name=str(skill_name or ""),
            kind=str(kind or "tool"),
            aliases=aliases,
            search_hint=str(search_hint or ""),
            read_only=inferred_read_only,
            destructive=inferred_destructive,
            allowed_modes=modes,
            should_defer=defer,
            always_load=load_always,
            runtime_binding=dict(runtime_binding or {}),
            skill_refs=list(skill_refs or ([skill_name] if skill_name else [])),
            metadata=dict(metadata or {}),
        )
        self.records[name] = record
        for alias in aliases:
            self.alias_to_name[alias] = name
        return record

    def resolve_name(self, name):
        text = str(name or "").strip()
        if text in self.records:
            return text
        return self.alias_to_name.get(text)

    def get(self, name):
        resolved = self.resolve_name(name)
        return self.records.get(resolved) if resolved else None

    def is_allowed(self, name, run_mode):
        record = self.get(name)
        if not record:
            return False
        mode = normalize_run_mode(run_mode)
        return mode in record.allowed_modes

    def is_visible(self, name, run_mode, discovered_tool_names=None):
        record = self.get(name)
        if not record or not self.is_allowed(name, run_mode):
            return False
        if record.always_load or not record.should_defer:
            return True
        discovered = {str(item or "").strip() for item in (discovered_tool_names or [])}
        return record.name in discovered

    def definitions(self, run_mode=None, discovered_tool_names=None, include_deferred=False):
        mode = normalize_run_mode(run_mode)
        definitions = []
        for name in sorted(self.records):
            record = self.records[name]
            if mode not in record.allowed_modes:
                continue
            if not include_deferred and not self.is_visible(name, mode, discovered_tool_names):
                continue
            definitions.append(record.to_definition())
        return definitions

    def search(self, query, run_mode=None, limit=8, include_loaded=False, discovered_tool_names=None):
        mode = normalize_run_mode(run_mode)
        query_tokens = _tokenize(query)
        discovered = {str(item or "").strip() for item in (discovered_tool_names or [])}
        if not query_tokens:
            query_tokens = ["tool"]
        results = []
        for record in self.records.values():
            if mode not in record.allowed_modes:
                continue
            if record.name == "tool_search":
                continue
            if not include_loaded and (record.always_load or record.name in discovered or not record.should_defer):
                continue
            score = self._score_record(record, query_tokens)
            if score <= 0:
                continue
            results.append((score, record.name, record))
        results.sort(key=lambda item: (-item[0], item[1]))
        max_results = max(1, int(limit or 8))
        return [self._search_payload(record, score) for score, _name, record in results[:max_results]]

    def _score_record(self, record, query_tokens):
        text_tokens = _tokenize(record.search_text())
        if not text_tokens:
            return 0
        frequencies = {}
        for token in text_tokens:
            frequencies[token] = frequencies.get(token, 0) + 1
        score = 0.0
        text_set = set(text_tokens)
        for token in query_tokens:
            if token in frequencies:
                score += 4.0 + math.log1p(frequencies[token])
                continue
            if any(token in item or item in token for item in text_set):
                score += 1.5
        if record.name in query_tokens:
            score += 8.0
        if record.search_hint:
            hint_tokens = set(_tokenize(record.search_hint))
            score += len(set(query_tokens) & hint_tokens) * 2.0
        return score

    def _search_payload(self, record, score):
        return {
            "name": record.name,
            "description": record.description,
            "skill_name": record.skill_name,
            "kind": record.kind,
            "aliases": list(record.aliases),
            "search_hint": record.search_hint,
            "read_only": record.read_only,
            "destructive": record.destructive,
            "allowed_modes": sorted(record.allowed_modes),
            "score": round(float(score), 3),
        }

    def _infer_read_only(self, name):
        if name in READ_ONLY_TOOL_NAMES:
            return True
        return name.startswith(("read_", "list_", "search_", "query_", "get_", "find_", "analyze_"))

    def _infer_destructive(self, name):
        if name in READ_ONLY_TOOL_NAMES:
            return False
        if name in EXECUTION_ONLY_TOOL_NAMES:
            return True
        return name.startswith(DESTRUCTIVE_PREFIXES)

    def _infer_always_load(self, name):
        return name in CORE_ALWAYS_LOAD_TOOLS or name in PLANNING_ALLOWED_TOOLS

    def _normalize_allowed_modes(self, allowed_modes, *, name, read_only):
        raw_modes = set(_as_string_list(allowed_modes))
        if not raw_modes:
            if name in PLANNING_ALLOWED_TOOLS or read_only:
                raw_modes = {RUN_MODE_EXECUTION, RUN_MODE_PLANNING}
            else:
                raw_modes = {RUN_MODE_EXECUTION}
        normalized = set()
        for mode in raw_modes:
            normalized.add(normalize_run_mode(mode))
        if RUN_MODE_NORMAL in raw_modes:
            normalized.add(RUN_MODE_EXECUTION)
        return normalized or {RUN_MODE_EXECUTION}
