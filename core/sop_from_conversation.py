import json
import re

from core.memory_update import collect_llm_content, estimate_tokens
from core.skill_from_conversation import render_session_transcript
from core.sop_manager import normalize_sop_template


DEFAULT_SOP_NAME = "对话生成的 SOP"


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
                value = re.split(r"[,，\n]+", value)
        except Exception:
            value = re.split(r"[,，\n]+", value)
    if not isinstance(value, list):
        return []
    result = []
    seen = set()
    for item in value:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _extract_json_object(text):
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("模型没有返回 SOP 草稿。")
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        parsed = json.loads(fenced.group(1))
        if isinstance(parsed, dict):
            return parsed

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        parsed = json.loads(raw[start : end + 1])
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("模型返回内容不是有效 SOP JSON。")


def _normalize_step(step, index=0):
    if not isinstance(step, dict):
        return None
    title = _string(step.get("title") or step.get("name"))
    instructions = _string(step.get("instructions") or step.get("prompt"))
    success_criteria = _string(step.get("success_criteria") or step.get("success"))
    if not title and not instructions and not success_criteria:
        return None
    return {
        "title": title or f"步骤 {index + 1}",
        "instructions": instructions,
        "success_criteria": success_criteria,
        "allow_skip": bool(step.get("allow_skip", False)),
    }


def normalize_sop_draft(payload, fallback_title=""):
    source = payload if isinstance(payload, dict) else {}
    name = _string(
        source.get("name")
        or source.get("template_name")
        or source.get("title")
        or fallback_title
        or DEFAULT_SOP_NAME
    )
    steps = []
    for index, step in enumerate(source.get("steps") or []):
        normalized = _normalize_step(step, index=index)
        if normalized:
            steps.append(normalized)
    if not steps:
        summary = _string(source.get("description") or source.get("summary"))
        if summary:
            steps.append(
                {
                    "title": "执行对话提炼流程",
                    "instructions": summary,
                    "success_criteria": "已按照对话中明确的流程完成任务。",
                    "allow_skip": False,
                }
            )
    draft = {
        "id": _string(source.get("id")),
        "name": name or DEFAULT_SOP_NAME,
        "description": _string(source.get("description") or source.get("summary")),
        "triggers": _string_list(source.get("triggers") or source.get("trigger_phrases")),
        "skill_names": _string_list(source.get("skill_names") or source.get("selected_skill_names")),
        "default_agent_profile_id": _string(source.get("default_agent_profile_id")),
        "steps": steps,
    }
    normalized = normalize_sop_template(draft)
    if normalized:
        return normalized
    return normalize_sop_template({**draft, "name": DEFAULT_SOP_NAME})


def build_sop_generation_messages(
    transcript,
    fallback_title="",
    previous_draft=None,
    revision_feedback="",
):
    system_prompt = (
        "你是 Cowork 的 SOP 流程设计助手。你的任务是从当前对话中一次性提炼可复用 SOP 草稿，"
        "只输出一个 JSON 对象，不要输出 Markdown 包裹。"
    )
    revision_section = ""
    if previous_draft or str(revision_feedback or "").strip():
        previous_json = json.dumps(previous_draft or {}, ensure_ascii=False, indent=2)
        revision_section = f"""

# 需要修订的上一版 SOP 草稿
{previous_json}

# 用户修改意见
{_string(revision_feedback)}
""".rstrip()

    user_prompt = f"""
请基于下面的会话记录提炼一个完整 SOP 草稿。

要求：
- 这是“在对话中创建 SOP”的流程：你必须一次性输出完整 SOP，不要把创建过程拆成逐步确认。
- SOP 应反映当前对话里已经形成的真实业务流程，而不是继续执行任务或复述聊天。
- 步骤应足够具体，方便之后绑定到会话或任务模板执行。
- 不要生成占位步骤，不要要求用户逐步确认每个步骤。
- 如果会话内容不足，仍给出最合理的简洁流程，并在 description 中说明依据有限。
- 名称优先使用中文，简短清楚。

输出 JSON 字段必须包含：
{{
  "name": "SOP 名称",
  "description": "SOP 目标和适用范围",
  "triggers": ["触发词"],
  "skill_names": [],
  "default_agent_profile_id": "",
  "steps": [
    {{
      "title": "步骤标题",
      "instructions": "执行指令",
      "success_criteria": "成功标准",
      "allow_skip": false
    }}
  ]
}}
{revision_section}

# 默认标题
{_string(fallback_title)}

# 会话记录
{transcript}
""".strip()
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def generate_sop_draft(
    provider,
    transcript,
    fallback_title="",
    previous_draft=None,
    revision_feedback="",
):
    messages = build_sop_generation_messages(
        transcript,
        fallback_title=fallback_title,
        previous_draft=previous_draft,
        revision_feedback=revision_feedback,
    )
    content = collect_llm_content(provider, messages)
    payload = _extract_json_object(content)
    draft = normalize_sop_draft(payload, fallback_title=fallback_title)
    draft["estimated_input_tokens"] = estimate_tokens(transcript)
    return draft
