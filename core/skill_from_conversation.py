import ast
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass

from core.env_utils import get_app_data_dir
from core.memory_update import collect_llm_content, estimate_tokens


DEFAULT_TRANSCRIPT_CHAR_LIMIT = 120_000
SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")


@dataclass
class SaveResult:
    ok: bool
    message: str
    path: str = ""


def is_valid_skill_name(skill_name):
    return bool(SKILL_NAME_RE.match(str(skill_name or "").strip()))


def _string(value, default=""):
    if value is None:
        return default
    return str(value).strip()


def _string_list(value):
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                value = parsed
            else:
                value = [value]
        except Exception:
            value = re.split(r"[\n,]+", value)
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _dict_list(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _slugify(text, default="conversation-skill"):
    slug = re.sub(r"[^a-z0-9-]+", "-", str(text or "").lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    if not slug:
        slug = default
    if not slug[0].isalnum():
        slug = default
    return slug[:64].strip("-") or default


def extract_impl_tool_refs(impl_py):
    if not str(impl_py or "").strip():
        return []
    try:
        tree = ast.parse(impl_py)
    except SyntaxError:
        return []
    refs = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            refs.append(node.name)
    return refs


def validate_impl_py(impl_py):
    code = str(impl_py or "").strip()
    if not code:
        return True, ""
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as exc:
        location = f"line {exc.lineno}, column {exc.offset}" if exc.lineno else "unknown location"
        return False, f"impl.py syntax error at {location}: {exc.msg}"


def render_session_transcript(session_id, title, messages, meta=None, char_limit=DEFAULT_TRANSCRIPT_CHAR_LIMIT):
    meta = meta if isinstance(meta, dict) else {}
    lines = [
        f"# 会话: {_string(title, '未命名会话')}",
        f"- ID: {_string(session_id)}",
    ]
    workspace = _string(meta.get("workspace_dir"))
    if workspace:
        lines.append(f"- 工作区: {workspace}")
    status = _string(meta.get("session_status") or meta.get("run_phase"))
    if status:
        lines.append(f"- 状态: {status}")
    lines.append("")

    for index, message in enumerate(messages or [], start=1):
        if not isinstance(message, dict):
            continue
        role = _string(message.get("role"), "unknown")
        lines.append(f"## {index}. {role}")
        content = _string(message.get("content"))
        if content:
            lines.append(content)
        if message.get("tool_calls"):
            try:
                calls = json.dumps(message.get("tool_calls"), ensure_ascii=False, indent=2)
            except Exception:
                calls = str(message.get("tool_calls"))
            lines.append("[tool_calls]")
            lines.append(calls)
        tool_call_id = _string(message.get("tool_call_id"))
        if tool_call_id:
            lines.append(f"[tool_call_id] {tool_call_id}")
        lines.append("")

    transcript = "\n".join(lines).strip()
    if len(transcript) <= char_limit:
        return transcript
    head = transcript[: int(char_limit * 0.65)]
    tail = transcript[-int(char_limit * 0.35) :]
    return f"{head}\n\n[...中间内容因过长已省略...]\n\n{tail}"


def _extract_json_object(text):
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("模型没有返回内容。")
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        return json.loads(fenced.group(1))

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        return json.loads(raw[start : end + 1])
    raise ValueError("模型返回内容不是有效 JSON。")


def normalize_skill_draft(payload, fallback_title="", mode="create"):
    payload = payload if isinstance(payload, dict) else {}
    description = _string(payload.get("description"), _string(fallback_title, "Conversation-derived skill"))
    skill_name = _string(payload.get("skill_name")) or _slugify(description or fallback_title)
    impl_py = _string(payload.get("impl_py"))
    tool_refs = _string_list(payload.get("tool_refs"))
    if impl_py and not tool_refs:
        tool_refs = extract_impl_tool_refs(impl_py)
    draft = {
        "mode": mode,
        "skill_name": _slugify(skill_name),
        "description": description,
        "description_cn": _string(payload.get("description_cn")),
        "usage_guidelines": _string(payload.get("usage_guidelines")),
        "tags": _string_list(payload.get("tags")),
        "triggers": _string_list(payload.get("triggers")),
        "workflow": _string_list(payload.get("workflow")),
        "experience_items": _string_list(payload.get("experience_items")),
        "tool_refs": tool_refs,
        "impl_py": impl_py,
    }
    if not draft["usage_guidelines"]:
        draft["usage_guidelines"] = "Use this skill when a future task matches the reusable patterns learned from the source conversation."
    return draft


def build_generation_messages(transcript, mode="create", target_skill=None, update_strategy="append"):
    mode_text = "生成新 Skill" if mode == "create" else f"更新已有 Skill: {target_skill or ''}"
    update_text = "追加经验" if update_strategy == "append" else "重写说明"
    system_prompt = (
        "你是 Cowork 的 Skill 工程助手。Skill 是结构化经验包，可以包含可选 impl.py 工具代码。"
        "你必须只输出一个 JSON 对象，不要输出 Markdown 包裹。"
    )
    user_prompt = f"""
请基于下面的会话记录{mode_text}，更新策略为：{update_text}。

要求：
- 提炼长期可复用的经验、触发场景、推荐流程，不要保存一次性闲聊或敏感信息。
- 生成新 Skill 时，如果会话里有明确可复用的自动化、文件处理、API 调用或脚本封装逻辑，可以生成 impl_py；否则 impl_py 置为空字符串。
- impl_py 必须是完整 Python 源码，只定义轻量函数和必要 import，不要执行顶层副作用。
- 更新已有 Skill 时默认不要生成 impl_py，除非会话非常明确要求新增工具代码。
- skill_name 使用 kebab-case，只能包含英文字母、数字和连字符。

输出 JSON 字段必须包含：
{{
  "skill_name": "kebab-case-name",
  "description": "English summary",
  "description_cn": "中文说明",
  "usage_guidelines": "SKILL.md body guidance",
  "tags": ["tag"],
  "triggers": ["trigger phrase"],
  "workflow": ["step"],
  "experience_items": ["lesson"],
  "tool_refs": ["function_name_if_impl_py_defines_it"],
  "impl_py": ""
}}

# 会话记录
{transcript}
""".strip()
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def generate_skill_draft(provider, transcript, mode="create", target_skill=None, update_strategy="append", fallback_title=""):
    messages = build_generation_messages(
        transcript,
        mode=mode,
        target_skill=target_skill,
        update_strategy=update_strategy,
    )
    content = collect_llm_content(provider, messages)
    payload = _extract_json_object(content)
    draft = normalize_skill_draft(payload, fallback_title=fallback_title, mode=mode)
    draft["estimated_input_tokens"] = estimate_tokens(transcript)
    return draft


def build_skill_body(draft):
    description = _string(draft.get("description"), "No description provided.")
    usage = _string(draft.get("usage_guidelines"))
    experience_items = _string_list(draft.get("experience_items"))
    workflow = _string_list(draft.get("workflow"))
    tools = _string_list(draft.get("tool_refs"))

    sections = [
        "# Skill Purpose\n" + description,
        "## When to Use\n" + (usage or "Use this skill when the task matches the source conversation patterns."),
        "## When Not to Use\nDo not use this skill for unrelated tasks or one-off details from the source conversation.",
        "## Common Pitfalls\nAvoid copying transient conversation details into future work unless they are clearly reusable.",
    ]
    if experience_items:
        sections.append("## Experience / Lessons Learned\n" + "\n".join(f"- {item}" for item in experience_items))
    else:
        sections.append("## Experience / Lessons Learned\nAdd reusable lessons here as the skill evolves.")
    if workflow:
        sections.append("## Recommended Workflow\n" + "\n".join(f"{idx}. {item}" for idx, item in enumerate(workflow, start=1)))
    else:
        sections.append("## Recommended Workflow\n1. Identify whether the task matches this skill.\n2. Apply the reusable guidance.\n3. Record new lessons after completion.")
    sections.append("## Recommended Tools\n" + ("\n".join(f"- `{tool}`" for tool in tools) if tools else "No dedicated tools are required."))
    sections.append("## Interface Details\n" + ("See `impl.py` for callable tool functions." if tools else "This is a knowledge-first skill."))
    sections.append("## Constraints and Safety Rules\nDo not store secrets, credentials, or full private transcripts in this skill.")
    return "\n\n".join(sections) + "\n"


def _frontmatter_value(value):
    if isinstance(value, list):
        return "[" + ", ".join(json.dumps(item, ensure_ascii=False) for item in value) + "]"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def build_skill_md(draft):
    skill_name = _string(draft.get("skill_name"))
    frontmatter = {
        "name": skill_name,
        "description": _string(draft.get("description"), "No description provided."),
        "license": "Apache-2.0",
        "type": "ai_generated",
        "created_by": "ai",
        "kind": "knowledge",
        "capability_group": "knowledge",
        "experience": _string_list(draft.get("experience_items")),
    }
    description_cn = _string(draft.get("description_cn"))
    if description_cn:
        frontmatter["description_cn"] = description_cn
    tool_refs = _string_list(draft.get("tool_refs"))
    if tool_refs:
        frontmatter["allowed-tools"] = tool_refs
    lines = [f"{key}: {_frontmatter_value(value)}" for key, value in frontmatter.items()]
    return f"---\n{chr(10).join(lines)}\n---\n\n{build_skill_body(draft)}"


def build_skill_json(draft):
    return {
        "version": 2,
        "name": _string(draft.get("skill_name")),
        "kind": "knowledge",
        "capability_group": "knowledge",
        "description": _string(draft.get("description"), "No description provided."),
        "tags": _string_list(draft.get("tags")),
        "triggers": _string_list(draft.get("triggers")),
        "anti_triggers": [],
        "references": [],
        "tool_refs": _string_list(draft.get("tool_refs")),
        "experience_policy": {
            "entry_storage": "experience/entries.jsonl",
            "summary_sync": "frontmatter_experience",
        },
        "disclosure_level_defaults": {
            "default_prompt_level": "brief",
            "include_references": False,
            "include_experience_entries": False,
        },
        "workflow": _string_list(draft.get("workflow")),
        "creation_hints": {
            "source": "conversation",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "python_dependencies": [],
        "node_dependencies": [],
        "script_refs": [],
        "script_entries": [],
        "asset_refs": [],
        "source_format": "cowork",
    }


def _atomic_write_text(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp.", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def _atomic_write_json(path, payload):
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def save_new_skill(draft, target_root=None):
    draft = normalize_skill_draft(draft)
    skill_name = draft["skill_name"]
    if not is_valid_skill_name(skill_name):
        return SaveResult(False, "Skill name must contain only letters, numbers, and hyphens.")

    impl_py = _string(draft.get("impl_py"))
    ok, error = validate_impl_py(impl_py)
    if not ok:
        return SaveResult(False, error)

    root = target_root or os.path.join(get_app_data_dir(), "ai_skills")
    target_dir = os.path.join(root, skill_name)
    if os.path.exists(target_dir):
        return SaveResult(False, f"Skill '{skill_name}' already exists.", target_dir)

    try:
        os.makedirs(os.path.join(target_dir, "experience"), exist_ok=False)
        _atomic_write_text(os.path.join(target_dir, "SKILL.md"), build_skill_md(draft))
        _atomic_write_json(os.path.join(target_dir, "skill.json"), build_skill_json(draft))
        if impl_py:
            _atomic_write_text(os.path.join(target_dir, "impl.py"), impl_py.rstrip() + "\n")
        return SaveResult(True, f"Skill '{skill_name}' created.", target_dir)
    except Exception as exc:
        return SaveResult(False, f"Failed to create skill: {exc}", target_dir)


def merge_skill_json_metadata(skill_record, tags=None, triggers=None):
    if not skill_record:
        return SaveResult(False, "Target skill not found.")
    skill_path = skill_record.get("path") or ""
    json_path = os.path.join(skill_path, "skill.json")
    payload = {}
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8-sig") as handle:
                parsed = json.load(handle)
            payload = parsed if isinstance(parsed, dict) else {}
        except Exception:
            payload = {}
    changed = False
    for key, additions in (("tags", tags), ("triggers", triggers)):
        current = _string_list(payload.get(key))
        for item in _string_list(additions):
            if item not in current:
                current.append(item)
                changed = True
        if current:
            payload[key] = current
    if not changed:
        return SaveResult(True, "No metadata changes.")
    payload.setdefault("version", 2)
    payload.setdefault("name", skill_record.get("name") or os.path.basename(skill_path))
    try:
        _atomic_write_json(json_path, payload)
        return SaveResult(True, "Skill metadata updated.", json_path)
    except Exception as exc:
        return SaveResult(False, f"Failed to update skill metadata: {exc}", json_path)


def update_existing_skill_from_draft(skill_manager, skill_name, draft, strategy="append"):
    if not skill_manager:
        return SaveResult(False, "SkillManager is not available.")
    if not skill_name:
        return SaveResult(False, "Target skill is required.")
    record = getattr(skill_manager, "skill_records", {}).get(skill_name)
    if not record:
        skill_manager.load_skills()
        record = getattr(skill_manager, "skill_records", {}).get(skill_name)
    if not record:
        return SaveResult(False, f"Skill '{skill_name}' not found.")

    draft = normalize_skill_draft(draft, fallback_title=skill_name, mode="update")
    strategy = (strategy or "append").strip().lower()
    if strategy == "rewrite":
        success, message = skill_manager.update_skill(
            skill_name,
            description=draft.get("description"),
            instructions=build_skill_body(draft),
        )
        if not success:
            return SaveResult(False, message, record.get("path") or "")
    else:
        items = _string_list(draft.get("experience_items"))
        if not items and draft.get("usage_guidelines"):
            items = [draft.get("usage_guidelines")]
        if not items:
            return SaveResult(False, "No experience items to append.", record.get("path") or "")
        for item in items:
            success, message = skill_manager.record_experience(
                experience_text=item,
                skill_name=skill_name,
                tags=_string_list(draft.get("tags")),
                source="conversation_skill",
            )
            if not success:
                return SaveResult(False, message, record.get("path") or "")

    metadata_result = merge_skill_json_metadata(record, tags=draft.get("tags"), triggers=draft.get("triggers"))
    if not metadata_result.ok:
        return metadata_result
    skill_manager.load_skills()
    return SaveResult(True, f"Skill '{skill_name}' updated.", record.get("path") or "")
