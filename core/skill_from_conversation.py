import ast
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import copy
import uuid
from dataclasses import asdict, dataclass, field

from core.env_utils import get_app_data_dir
from core.memory_update import collect_llm_content, estimate_tokens


DEFAULT_TRANSCRIPT_CHAR_LIMIT = 120_000
DEFAULT_EVIDENCE_CHAR_LIMIT = 120_000
SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")
WINDOWS_USER_PATH_RE = re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\s\"']+")
WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"(?i)\b[A-Z]:\\(?:[^\\\r\n\"']+\\)*[^\\\r\n\"']*")
WINDOWS_FORWARD_PATH_RE = re.compile(r"(?i)\b[A-Z]:/(?:[^/\s\"']+/)*[^/\s\"']*")
UNIX_USER_PATH_RE = re.compile(r"(?i)(?:/home/[^/\s\"']+|/Users/[^/\s\"']+)(?:/[^\s\"']*)?")
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|passwd)"
    r"(\s*[:=]\s*)([^\s,;\"']+|\"[^\"]*\"|'[^']*')"
)
BEARER_TOKEN_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")


@dataclass
class SaveResult:
    ok: bool
    message: str
    path: str = ""


@dataclass
class ConversationSkillEvidence:
    version: int = 1
    task_goal: dict = field(default_factory=dict)
    outcome: dict = field(default_factory=dict)
    reusable_patterns: list = field(default_factory=list)
    variables: list = field(default_factory=list)
    constraints: list = field(default_factory=list)
    decision_rules: list = field(default_factory=list)
    workflow_steps: list = field(default_factory=list)
    failure_lessons: list = field(default_factory=list)
    verification_methods: list = field(default_factory=list)
    resource_candidates: list = field(default_factory=list)
    missing_evidence: list = field(default_factory=list)
    privacy_findings: list = field(default_factory=list)
    source_message_ids: list = field(default_factory=list)
    omitted_message_ids: list = field(default_factory=list)
    invalid_source_refs: list = field(default_factory=list)
    confidence: str = "low"
    suggested_name: str = ""
    suggested_description: str = ""
    source_digest: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class ConversationSkillDraftV2:
    version: int = 2
    skill_name: str = ""
    description: str = ""
    description_cn: str = ""
    tags: list = field(default_factory=list)
    triggers: list = field(default_factory=list)
    anti_triggers: list = field(default_factory=list)
    instructions_md: str = ""
    workflow: list = field(default_factory=list)
    experience_items: list = field(default_factory=list)
    tool_refs: list = field(default_factory=list)
    impl_py: str = ""
    resources: list = field(default_factory=list)
    script_assets: list = field(default_factory=list)
    python_dependencies: list = field(default_factory=list)
    node_dependencies: list = field(default_factory=list)
    quality: str = "low"
    missing_evidence: list = field(default_factory=list)
    change_summary: list = field(default_factory=list)
    capture_id: str = ""
    source_session_id: str = ""
    source_message_ids: list = field(default_factory=list)
    source_digest: str = ""
    target_revision: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class DraftValidationIssue:
    severity: str
    code: str
    message: str
    resource_path: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class DraftValidationResult:
    ok: bool
    issues: list = field(default_factory=list)

    def to_dict(self):
        return {
            "ok": bool(self.ok),
            "issues": [
                item.to_dict() if isinstance(item, DraftValidationIssue) else dict(item)
                for item in self.issues
            ],
        }


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


def _json_value(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _stable_message_id(message, index):
    value = str((message or {}).get("id") or "").strip()
    return value or f"message-{index:04d}"


def _source_refs(value):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return _string_list(value)


def _claim_text(value):
    if isinstance(value, dict):
        return _string(value.get("text") or value.get("claim") or value.get("value"))
    return _string(value)


def _sanitize_text(text, workspace_dir="", message_id=""):
    sanitized = str(text or "")
    findings = []

    def record(kind):
        finding = {"kind": kind}
        if message_id:
            finding["source_message_id"] = message_id
        if finding not in findings:
            findings.append(finding)

    def replace_secret(match):
        record("secret")
        return f"{match.group(1)}{match.group(2)}<redacted-secret>"

    sanitized = SECRET_ASSIGNMENT_RE.sub(replace_secret, sanitized)
    if BEARER_TOKEN_RE.search(sanitized):
        record("bearer_token")
        sanitized = BEARER_TOKEN_RE.sub("Bearer <redacted-secret>", sanitized)

    normalized_workspace = str(workspace_dir or "").strip()
    if normalized_workspace:
        workspace_variants = {
            normalized_workspace,
            normalized_workspace.replace("\\", "/"),
            normalized_workspace.replace("/", "\\"),
        }
        for value in sorted(workspace_variants, key=len, reverse=True):
            if value and value.lower() in sanitized.lower():
                record("workspace_path")
                sanitized = re.sub(re.escape(value), "<workspace>", sanitized, flags=re.IGNORECASE)

    if WINDOWS_USER_PATH_RE.search(sanitized):
        record("user_home_path")
        sanitized = WINDOWS_USER_PATH_RE.sub("<user-home>", sanitized)
    if WINDOWS_FORWARD_PATH_RE.search(sanitized):
        record("local_absolute_path")
        sanitized = WINDOWS_FORWARD_PATH_RE.sub("<local-path>", sanitized)
    if UNIX_USER_PATH_RE.search(sanitized):
        record("user_home_path")
        sanitized = UNIX_USER_PATH_RE.sub("<user-home>", sanitized)
    return sanitized, findings


def sanitize_conversation_messages(messages, workspace_dir=""):
    sanitized_messages = []
    findings = []
    for index, raw_message in enumerate(messages or [], start=1):
        if not isinstance(raw_message, dict):
            continue
        message = copy.deepcopy(raw_message)
        message_id = _stable_message_id(message, index)
        message["id"] = message_id
        content, content_findings = _sanitize_text(
            message.get("content"),
            workspace_dir=workspace_dir,
            message_id=message_id,
        )
        message["content"] = content
        findings.extend(content_findings)
        if message.get("tool_calls"):
            sanitized_calls = []
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                cloned = copy.deepcopy(call)
                function = cloned.get("function") if isinstance(cloned.get("function"), dict) else {}
                arguments = function.get("arguments")
                if arguments is not None:
                    sanitized_args, arg_findings = _sanitize_text(
                        arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False),
                        workspace_dir=workspace_dir,
                        message_id=message_id,
                    )
                    function["arguments"] = sanitized_args
                    findings.extend(arg_findings)
                cloned["function"] = function
                sanitized_calls.append(cloned)
            message["tool_calls"] = sanitized_calls
        sanitized_messages.append(message)
    unique_findings = []
    for item in findings:
        if item not in unique_findings:
            unique_findings.append(item)
    return sanitized_messages, unique_findings


def _render_evidence_message(message, index):
    message_id = _stable_message_id(message, index)
    role = _string(message.get("role"), "unknown")
    lines = [f"## {index}. {role}", f"[message_id] {message_id}"]
    content = _string(message.get("content"))
    if content:
        lines.append(content)
    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        safe_tool_calls = []
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            cloned = copy.deepcopy(call)
            function = cloned.get("function") if isinstance(cloned.get("function"), dict) else {}
            if _string(function.get("name")) == "run_python_code":
                arguments = _json_value(function.get("arguments"))
                if isinstance(arguments, dict) and "code" in arguments:
                    arguments["code"] = "<executed-code-omitted; rewrite from purpose>"
                    function["arguments"] = json.dumps(arguments, ensure_ascii=False)
            cloned["function"] = function
            safe_tool_calls.append(cloned)
        lines.append("[tool_calls]")
        lines.append(json.dumps(safe_tool_calls, ensure_ascii=False, indent=2))
    tool_call_id = _string(message.get("tool_call_id"))
    if tool_call_id:
        lines.append(f"[tool_call_id] {tool_call_id}")
    attachments = message.get("attachments") or []
    if attachments:
        safe_attachments = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            safe_attachments.append(
                {
                    "name": _string(attachment.get("name") or attachment.get("filename")),
                    "kind": _string(attachment.get("kind") or attachment.get("mime_type")),
                    "size": attachment.get("size"),
                }
            )
        if safe_attachments:
            lines.append("[attachments]")
            lines.append(json.dumps(safe_attachments, ensure_ascii=False))
    return "\n".join(lines).strip()


def build_evidence_source(
    session_id,
    title,
    messages,
    meta=None,
    char_limit=DEFAULT_EVIDENCE_CHAR_LIMIT,
):
    meta = meta if isinstance(meta, dict) else {}
    workspace_dir = _string(meta.get("workspace_dir"))
    sanitized_messages, privacy_findings = sanitize_conversation_messages(
        messages,
        workspace_dir=workspace_dir,
    )
    blocks = [
        {
            "message_id": _stable_message_id(message, index),
            "text": _render_evidence_message(message, index),
        }
        for index, message in enumerate(sanitized_messages, start=1)
    ]
    header = "\n".join(
        [
            f"# 会话: {_string(title, '未命名会话')}",
            f"- ID: {_string(session_id)}",
            "- 工作区: <workspace>" if workspace_dir else "",
        ]
    ).strip()
    full_text = "\n\n".join([header, *[item["text"] for item in blocks]]).strip()
    included = list(blocks)
    omitted_ids = []
    limit = max(1000, int(char_limit or DEFAULT_EVIDENCE_CHAR_LIMIT))
    if len(full_text) > limit:
        included = []
        used = len(header) + 64
        front_budget = int(limit * 0.58)
        for block in blocks:
            cost = len(block["text"]) + 2
            if used + cost > front_budget:
                break
            included.append(block)
            used += cost
        included_ids = {item["message_id"] for item in included}
        for block in reversed(blocks):
            if block["message_id"] in included_ids:
                continue
            cost = len(block["text"]) + 2
            if used + cost > limit:
                continue
            included.append(block)
            included_ids.add(block["message_id"])
            used += cost
        included.sort(key=lambda item: next(
            index for index, original in enumerate(blocks)
            if original["message_id"] == item["message_id"]
        ))
        omitted_ids = [
            item["message_id"] for item in blocks
            if item["message_id"] not in included_ids
        ]
    truncation_notice = ""
    if omitted_ids:
        truncation_notice = (
            "\n\n[明确裁剪]\n"
            f"以下消息因输入预算未提供给模型：{', '.join(omitted_ids)}"
        )
    text = "\n\n".join([header, *[item["text"] for item in included]]).strip() + truncation_notice
    all_ids = [item["message_id"] for item in blocks]
    return {
        "session_id": _string(session_id),
        "title": _string(title, "未命名会话"),
        "text": text,
        "source_message_ids": all_ids,
        "included_message_ids": [item["message_id"] for item in included],
        "omitted_message_ids": omitted_ids,
        "privacy_findings": privacy_findings,
        "source_digest": hashlib.sha256(full_text.encode("utf-8")).hexdigest(),
        "estimated_input_tokens": estimate_tokens(text),
    }


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
        tree = ast.parse(code)
    except SyntaxError as exc:
        location = f"line {exc.lineno}, column {exc.offset}" if exc.lineno else "unknown location"
        return False, f"impl.py syntax error at {location}: {exc.msg}"
    def _literal_only(node):
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return all(_literal_only(item) for item in node.elts)
        if isinstance(node, ast.Dict):
            return all(_literal_only(item) for item in [*node.keys, *node.values] if item is not None)
        return False

    allowed_nodes = (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    for node in tree.body:
        if isinstance(node, allowed_nodes):
            continue
        if isinstance(node, ast.Assign) and _literal_only(node.value):
            continue
        if isinstance(node, ast.AnnAssign) and (node.value is None or _literal_only(node.value)):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        return False, "impl.py must not execute top-level code; keep only imports, constants, functions, and classes."
    return True, ""


def extract_python_script_assets(messages, limit=12):
    assets = []
    seen = set()
    tool_results = {}
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "tool" and message.get("tool_call_id"):
            tool_results[str(message.get("tool_call_id"))] = str(message.get("content") or "")[:2000]

    for message in messages or []:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            if function.get("name") != "run_python_code":
                continue
            args = _json_value(function.get("arguments"))
            if not isinstance(args, dict):
                continue
            code = str(args.get("code") or "").strip()
            if not code:
                continue
            key = re.sub(r"\s+", "\n", code).strip()
            if key in seen:
                continue
            seen.add(key)
            index = len(assets) + 1
            ok, error = validate_impl_py(code)
            if not ok:
                try:
                    ast.parse(code)
                    ok, error = True, ""
                except SyntaxError as exc:
                    location = f"line {exc.lineno}, column {exc.offset}" if exc.lineno else "unknown location"
                    error = f"Python syntax error at {location}: {exc.msg}"
            tool_call_id = str(call.get("id") or "")
            assets.append(
                {
                    "name": f"run_python_{index:03d}",
                    "path": f"scripts/run_python_{index:03d}.py",
                    "runtime": "python",
                    "description": "Python code captured from this conversation.",
                    "code": code,
                    "source_tool_call_id": tool_call_id,
                    "cwd": str(args.get("cwd") or ""),
                    "result_excerpt": tool_results.get(tool_call_id, ""),
                    "valid": bool(ok),
                    "error": error,
                    "selected": False,
                }
            )
            if len(assets) >= int(limit or 12):
                return assets
    return assets


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


def build_evidence_extraction_messages(source_bundle):
    system_prompt = (
        "你是 Cowork 的可复用证据分析器。只提取会话中有明确 message_id 支持的事实，"
        "不要编写 Skill 文案、不要生成代码、不要补全会话没有证明的步骤。"
        "只输出一个 JSON 对象，不要使用 Markdown 包裹。"
    )
    user_prompt = f"""
从下面经过脱敏的会话中，提取未来同类任务可以复用的证据。

规则：
- 每个结论都必须包含 source_message_ids，且只能引用输入中出现的 message_id。
- 把硬编码路径、文件名、账号、日期和数值抽象为变量。
- 区分稳定规律、约束、决策规则、执行步骤、失败教训和验证方法。
- 没有证据的内容写入 missing_evidence，不要猜测。
- resource_candidates 只描述可能值得参数化的 script/reference/tool，不输出代码。

输出结构：
{{
  "task_goal": {{"text": "", "source_message_ids": []}},
  "outcome": {{"text": "", "source_message_ids": []}},
  "reusable_patterns": [{{"text": "", "source_message_ids": []}}],
  "variables": [{{"name": "", "generalized_as": "", "source_message_ids": []}}],
  "constraints": [{{"text": "", "source_message_ids": []}}],
  "decision_rules": [{{"text": "", "source_message_ids": []}}],
  "workflow_steps": [{{"text": "", "source_message_ids": []}}],
  "failure_lessons": [{{"text": "", "source_message_ids": []}}],
  "verification_methods": [{{"text": "", "source_message_ids": []}}],
  "resource_candidates": [
    {{"kind": "script|reference|tool", "description": "", "source_message_ids": []}}
  ],
  "missing_evidence": [""],
  "privacy_findings": [""],
  "suggested_name": "kebab-case-name",
  "suggested_description": ""
}}

# 脱敏会话
{source_bundle.get("text") or ""}
""".strip()
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _normalize_evidence_claim(value, valid_ids, invalid_refs):
    if not isinstance(value, dict):
        value = {"text": _string(value), "source_message_ids": []}
    text = _claim_text(value)
    refs = _source_refs(value.get("source_message_ids"))
    accepted = []
    for ref in refs:
        if ref in valid_ids:
            accepted.append(ref)
        elif ref not in invalid_refs:
            invalid_refs.append(ref)
    if not text:
        return {}
    return {"text": text, "source_message_ids": accepted}


def _normalize_evidence_claims(value, valid_ids, invalid_refs):
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        claim = _normalize_evidence_claim(item, valid_ids, invalid_refs)
        if claim and claim not in result:
            result.append(claim)
    return result


def _normalize_evidence_variables(value, valid_ids, invalid_refs):
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if not isinstance(item, dict):
            continue
        refs = _source_refs(item.get("source_message_ids"))
        accepted = []
        for ref in refs:
            if ref in valid_ids:
                accepted.append(ref)
            elif ref not in invalid_refs:
                invalid_refs.append(ref)
        normalized = {
            "name": _string(item.get("name")),
            "generalized_as": _string(item.get("generalized_as") or item.get("value")),
            "source_message_ids": accepted,
        }
        if normalized["name"] and normalized not in result:
            result.append(normalized)
    return result


def _normalize_resource_candidates(value, valid_ids, invalid_refs):
    if not isinstance(value, list):
        return []
    result = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue
        kind = _string(item.get("kind"), "reference").lower()
        if kind not in {"script", "reference", "tool"}:
            kind = "reference"
        refs = _source_refs(item.get("source_message_ids"))
        accepted = []
        for ref in refs:
            if ref in valid_ids:
                accepted.append(ref)
            elif ref not in invalid_refs:
                invalid_refs.append(ref)
        description = re.sub(
            r"\s+",
            " ",
            _string(item.get("description") or item.get("text")),
        )[:500].strip()
        if not description:
            continue
        normalized = {
            "id": _string(item.get("id"), f"resource-{index:03d}"),
            "kind": kind,
            "description": description,
            "source_message_ids": accepted,
            "selected": False,
        }
        if normalized not in result:
            result.append(normalized)
    return result


def normalize_conversation_skill_evidence(payload, source_bundle):
    payload = payload if isinstance(payload, dict) else {}
    valid_ids = set(_string_list(source_bundle.get("included_message_ids")))
    invalid_refs = []
    goal = _normalize_evidence_claim(payload.get("task_goal"), valid_ids, invalid_refs)
    outcome = _normalize_evidence_claim(payload.get("outcome"), valid_ids, invalid_refs)
    patterns = _normalize_evidence_claims(payload.get("reusable_patterns"), valid_ids, invalid_refs)
    constraints = _normalize_evidence_claims(payload.get("constraints"), valid_ids, invalid_refs)
    decisions = _normalize_evidence_claims(payload.get("decision_rules"), valid_ids, invalid_refs)
    workflow = _normalize_evidence_claims(payload.get("workflow_steps"), valid_ids, invalid_refs)
    failures = _normalize_evidence_claims(payload.get("failure_lessons"), valid_ids, invalid_refs)
    verification = _normalize_evidence_claims(payload.get("verification_methods"), valid_ids, invalid_refs)
    variables = _normalize_evidence_variables(payload.get("variables"), valid_ids, invalid_refs)
    resources = _normalize_resource_candidates(payload.get("resource_candidates"), valid_ids, invalid_refs)

    omitted_ids = _string_list(source_bundle.get("omitted_message_ids"))
    missing_evidence = _string_list(payload.get("missing_evidence"))
    if omitted_ids:
        missing_evidence.append("部分所选消息因输入预算未提供给证据分析器。")
    if invalid_refs:
        missing_evidence.append("模型返回了无法对应到所选会话的证据引用。")
    privacy_findings = list(source_bundle.get("privacy_findings") or [])
    model_privacy_flags = _string_list(payload.get("privacy_findings"))
    for item in model_privacy_flags:
        privacy_findings.append({"kind": "model_flag", "message": item})

    has_goal = bool(goal and goal.get("source_message_ids"))
    has_pattern = any(item.get("source_message_ids") for item in patterns)
    has_execution = any(item.get("source_message_ids") for item in [*decisions, *workflow])
    has_outcome = bool(outcome and outcome.get("source_message_ids"))
    has_verification = any(item.get("source_message_ids") for item in verification)
    if model_privacy_flags:
        confidence = "low"
    elif (
        has_goal
        and has_pattern
        and has_execution
        and has_outcome
        and has_verification
        and not omitted_ids
        and not invalid_refs
    ):
        confidence = "high"
    elif has_goal and has_pattern and not omitted_ids:
        confidence = "medium"
    else:
        confidence = "low"

    evidence = ConversationSkillEvidence(
        task_goal=goal,
        outcome=outcome,
        reusable_patterns=patterns,
        variables=variables,
        constraints=constraints,
        decision_rules=decisions,
        workflow_steps=workflow,
        failure_lessons=failures,
        verification_methods=verification,
        resource_candidates=resources,
        missing_evidence=_string_list(missing_evidence),
        privacy_findings=privacy_findings,
        source_message_ids=_string_list(source_bundle.get("source_message_ids")),
        omitted_message_ids=omitted_ids,
        invalid_source_refs=invalid_refs,
        confidence=confidence,
        suggested_name=_slugify(payload.get("suggested_name") or _claim_text(goal)),
        suggested_description=_string(payload.get("suggested_description") or _claim_text(goal)),
        source_digest=_string(source_bundle.get("source_digest")),
    )
    return evidence.to_dict()


def extract_conversation_skill_evidence(provider, source_bundle):
    messages = build_evidence_extraction_messages(source_bundle)
    content = collect_llm_content(provider, messages, max_retries=1)
    payload = _extract_json_object(content)
    return normalize_conversation_skill_evidence(payload, source_bundle)


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
        "instructions_md": _string(payload.get("instructions_md") or payload.get("instructions")),
        "tags": _string_list(payload.get("tags")),
        "triggers": _string_list(payload.get("triggers")),
        "anti_triggers": _string_list(payload.get("anti_triggers")),
        "workflow": _string_list(payload.get("workflow")),
        "experience_items": _string_list(payload.get("experience_items")),
        "tool_refs": tool_refs,
        "impl_py": impl_py,
        "resources": _dict_list(payload.get("resources")),
        "script_assets": _dict_list(payload.get("script_assets")),
        "python_dependencies": _string_list(payload.get("python_dependencies")),
        "node_dependencies": _string_list(payload.get("node_dependencies")),
        "quality": _string(payload.get("quality"), "low").lower(),
        "missing_evidence": _string_list(payload.get("missing_evidence")),
        "change_summary": _string_list(payload.get("change_summary")),
        "capture_id": _string(payload.get("capture_id")),
        "source_session_id": _string(payload.get("source_session_id")),
        "source_message_ids": _string_list(payload.get("source_message_ids")),
        "source_digest": _string(payload.get("source_digest")),
        "target_revision": _string(payload.get("target_revision")),
    }
    if draft["quality"] not in {"high", "medium", "low"}:
        draft["quality"] = "low"
    if not draft["usage_guidelines"] and not draft["instructions_md"]:
        draft["usage_guidelines"] = "Use this skill when a future task matches the reusable patterns learned from the source conversation."
    return draft


def compute_skill_revision(skill_path):
    base_path = os.path.abspath(str(skill_path or ""))
    if not base_path or not os.path.isdir(base_path):
        return ""
    digest = hashlib.sha256()
    ignored_dirs = {".git", ".venv", "__pycache__", "node_modules"}
    files = []
    for current_root, dirnames, filenames in os.walk(base_path):
        dirnames[:] = sorted(name for name in dirnames if name not in ignored_dirs)
        for filename in sorted(filenames):
            if filename.endswith((".pyc", ".pyo")) or filename.startswith(".tmp."):
                continue
            path = os.path.join(current_root, filename)
            files.append((os.path.relpath(path, base_path).replace("\\", "/"), path))
    for relative_path, path in sorted(files):
        digest.update(relative_path.encode("utf-8"))
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(64 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
    return digest.hexdigest()


def build_target_skill_snapshot(skill_record):
    if not isinstance(skill_record, dict):
        return {}
    spec = skill_record.get("spec") if isinstance(skill_record.get("spec"), dict) else {}
    meta = skill_record.get("meta") if isinstance(skill_record.get("meta"), dict) else {}
    safe_spec_keys = {
        "name",
        "description",
        "tags",
        "triggers",
        "anti_triggers",
        "workflow",
        "tool_refs",
        "script_refs",
        "script_entries",
        "references",
        "python_dependencies",
        "node_dependencies",
        "capability_group",
        "kind",
    }
    safe_meta_keys = {
        "name",
        "description",
        "description_cn",
        "experience",
        "kind",
        "capability_group",
    }
    experience = []
    for item in (skill_record.get("experience_entries") or [])[-10:]:
        if not isinstance(item, dict):
            continue
        text = _string(item.get("experience_text"))
        if text:
            experience.append({"experience_text": text, "tags": _string_list(item.get("tags"))})
    return {
        "name": _string(skill_record.get("name") or meta.get("name") or spec.get("name")),
        "body": _string(skill_record.get("body")),
        "meta": {key: copy.deepcopy(meta.get(key)) for key in safe_meta_keys if key in meta},
        "spec": {key: copy.deepcopy(spec.get(key)) for key in safe_spec_keys if key in spec},
        "recent_experience": experience,
        "revision": compute_skill_revision(skill_record.get("path")),
    }


def build_skill_compilation_messages(
    evidence,
    mode="create",
    target_skill_snapshot=None,
    update_strategy="merge_guidance",
    selected_resources=None,
    confirmed_analysis="",
):
    evidence = evidence if isinstance(evidence, dict) else {}
    strategy = "merge_guidance" if update_strategy == "rewrite" else update_strategy
    target_snapshot = target_skill_snapshot if isinstance(target_skill_snapshot, dict) else {}
    selected_resources = _dict_list(selected_resources)
    confirmed_analysis = _string(confirmed_analysis)
    system_prompt = (
        "你是 Cowork 的 Skill 编译器。只根据经过引用校验的证据生成面向未来同类任务的 Skill，"
        "不要复述来源会话，不要发明缺失步骤。正文使用简洁的祈使句。"
        "只输出一个 JSON 对象，不要使用 Markdown 包裹。"
    )
    user_prompt = f"""
把下面的证据编译成 Cowork Skill 草稿。

规则：
- description 必须同时说明能力和触发场景。
- instructions_md 只保留执行所需的决策规则、流程、验证和失败处理。
- 不要创建通用空话章节，不要写来源会话、生成过程或证据 ID。
- anti_triggers 明确不适用场景。
- 所有路径、文件名、账号、日期和数值都必须参数化。
- 只有 selected_resources 中的候选可以生成资源内容；资源路径只能位于 scripts/ 或 references/。
- script 必须根据候选用途重新编写为参数化实现，声明清晰输入输出并校验输入；禁止复刻会话中执行过的原始代码，禁止顶层执行。
- 如果证据不足，保持正文克制，并把缺失项原样放进 missing_evidence。
- 更新策略为 append_experience 时，不重写已有正文，instructions_md 返回空字符串。
- 更新策略为 merge_guidance 时，基于目标正文生成完整修订正文，并保留无关能力。
- “用户确认后的沉淀意见”是本次编译的内容意图最高优先级；存在时必须按其修改目标、规律、边界和流程。
- 原始证据仍用于来源、置信度和隐私约束；用户确认文本不能用于绕过安全校验或生成未选择的资源。

输出结构：
{{
  "skill_name": "kebab-case-name",
  "description": "English capability and when-to-use summary",
  "description_cn": "中文能力与触发场景说明",
  "tags": [],
  "triggers": [],
  "anti_triggers": [],
  "instructions_md": "",
  "workflow": [],
  "experience_items": [],
  "tool_refs": [],
  "impl_py": "",
  "python_dependencies": [],
  "node_dependencies": [],
  "resources": [
    {{
      "kind": "script|reference",
      "path": "scripts/name.py",
      "description": "",
      "content": "",
      "source_message_ids": []
    }}
  ],
  "change_summary": []
}}

# 模式
mode: {mode}
update_strategy: {strategy}

# 用户确认后的沉淀意见
{confirmed_analysis or "未提供额外修改，按原始可复用证据编译。"}

# 可复用证据
{json.dumps(evidence, ensure_ascii=False, indent=2)}

# 用户选择的资源候选
{json.dumps(selected_resources, ensure_ascii=False, indent=2)}

# 目标 Skill 快照
{json.dumps(target_snapshot, ensure_ascii=False, indent=2)}
""".strip()
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def compile_conversation_skill_draft(
    provider,
    evidence,
    mode="create",
    target_skill_snapshot=None,
    update_strategy="merge_guidance",
    selected_resources=None,
    confirmed_analysis="",
    capture_id="",
    source_session_id="",
):
    messages = build_skill_compilation_messages(
        evidence,
        mode=mode,
        target_skill_snapshot=target_skill_snapshot,
        update_strategy=update_strategy,
        selected_resources=selected_resources,
        confirmed_analysis=confirmed_analysis,
    )
    content = collect_llm_content(provider, messages, max_retries=1)
    payload = _extract_json_object(content)
    payload["quality"] = _string((evidence or {}).get("confidence"), "low")
    payload["missing_evidence"] = _string_list((evidence or {}).get("missing_evidence"))
    payload["capture_id"] = _string(capture_id)
    payload["source_session_id"] = _string(source_session_id)
    payload["source_message_ids"] = _string_list((evidence or {}).get("source_message_ids"))
    payload["source_digest"] = _string((evidence or {}).get("source_digest"))
    payload["target_revision"] = _string((target_skill_snapshot or {}).get("revision"))
    draft = normalize_skill_draft(
        payload,
        fallback_title=_string((evidence or {}).get("suggested_description")),
        mode=mode,
    )
    if mode == "update" and target_skill_snapshot:
        draft["skill_name"] = _string(target_skill_snapshot.get("name"), draft["skill_name"])
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
    instructions = _string(draft.get("instructions_md"))
    if instructions:
        return instructions.rstrip() + "\n"
    usage = _string(draft.get("usage_guidelines"))
    experience_items = _string_list(draft.get("experience_items"))
    workflow = _string_list(draft.get("workflow"))
    sections = []
    if usage:
        sections.append(usage)
    if workflow:
        sections.append(
            "## Workflow\n"
            + "\n".join(f"{idx}. {item}" for idx, item in enumerate(workflow, start=1))
        )
    if experience_items:
        sections.append("## Reusable Lessons\n" + "\n".join(f"- {item}" for item in experience_items))
    if not sections:
        sections.append("Apply the reusable workflow described by this Skill's metadata.")
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
    script_assets = normalize_script_assets(draft.get("script_assets"))
    resources = normalize_draft_resources(draft.get("resources"))
    for resource in resources:
        if resource["kind"] == "script" and resource["path"].endswith(".py"):
            script_assets.append(
                {
                    "name": _slugify(
                        os.path.splitext(os.path.basename(resource["path"]))[0],
                        default="conversation-script",
                    ).replace("-", "_"),
                    "path": resource["path"],
                    "runtime": "python",
                    "description": resource.get("description") or "",
                    "code": resource.get("content") or "",
                }
            )
    script_assets = normalize_script_assets(script_assets)
    script_entries = [
        {
            "name": item["name"],
            "path": item["path"],
            "runtime": item.get("runtime") or "python",
            "description": item.get("description") or "",
        }
        for item in script_assets
    ]
    return {
        "version": 2,
        "name": _string(draft.get("skill_name")),
        "kind": "knowledge",
        "capability_group": "knowledge",
        "description": _string(draft.get("description"), "No description provided."),
        "tags": _string_list(draft.get("tags")),
        "triggers": _string_list(draft.get("triggers")),
        "anti_triggers": _string_list(draft.get("anti_triggers")),
        "references": [
            item["path"] for item in resources if item["kind"] == "reference"
        ],
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
            "capture_id": _string(draft.get("capture_id")),
            "source_session_id": _string(draft.get("source_session_id")),
            "source_message_ids": _string_list(draft.get("source_message_ids")),
            "source_digest": _string(draft.get("source_digest")),
            "confidence": _string(draft.get("quality"), "low"),
        },
        "python_dependencies": _string_list(draft.get("python_dependencies")),
        "node_dependencies": _string_list(draft.get("node_dependencies")),
        "script_refs": [item["path"] for item in script_assets],
        "script_entries": script_entries,
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


def normalize_draft_resources(value):
    resources = []
    seen_paths = set()
    for item in _dict_list(value):
        kind = _string(item.get("kind"), "reference").lower()
        if kind not in {"script", "reference"}:
            continue
        path = _string(item.get("path")).replace("\\", "/").strip("/")
        expected_prefix = "scripts/" if kind == "script" else "references/"
        if not path.startswith(expected_prefix):
            continue
        if ".." in path.split("/") or path.lower() in seen_paths:
            continue
        content = str(item.get("content") or "")
        if not content.strip():
            continue
        seen_paths.add(path.lower())
        resources.append(
            {
                "kind": kind,
                "path": path,
                "description": _string(item.get("description")),
                "content": content,
                "source_message_ids": _source_refs(item.get("source_message_ids")),
                "selected": bool(item.get("selected", True)),
            }
        )
    return resources


def normalize_script_assets(value):
    assets = []
    seen_paths = set()
    for index, item in enumerate(_dict_list(value), start=1):
        code = str(item.get("code") or "").strip()
        if not code:
            continue
        try:
            ast.parse(code)
        except SyntaxError:
            continue
        name = _slugify(item.get("name") or f"run-python-{index:03d}", default=f"run-python-{index:03d}").replace("-", "_")
        path = str(item.get("path") or f"scripts/{name}.py").replace("\\", "/").strip("/")
        if not path.startswith("scripts/") or not path.endswith(".py") or ".." in path.split("/"):
            path = f"scripts/{name}.py"
        if path.lower() in seen_paths:
            continue
        seen_paths.add(path.lower())
        assets.append(
            {
                "name": name,
                "path": path,
                "runtime": "python",
                "description": str(item.get("description") or "Python code captured from a conversation.").strip(),
                "code": code,
            }
        )
    return assets


def write_script_assets(skill_dir, draft):
    assets = normalize_script_assets(draft.get("script_assets"))
    for item in assets:
        _atomic_write_text(os.path.join(skill_dir, item["path"]), item["code"].rstrip() + "\n")
    return assets


def write_draft_resources(skill_dir, draft):
    resources = normalize_draft_resources(draft.get("resources"))
    for item in resources:
        _atomic_write_text(
            os.path.join(skill_dir, item["path"]),
            item["content"].rstrip() + "\n",
        )
    return resources


def _python_import_roots(code):
    try:
        tree = ast.parse(str(code or ""))
    except SyntaxError:
        return set()
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".", 1)[0])
    return roots


def _declared_python_import_names(dependencies):
    package_import_aliases = {
        "pyyaml": "yaml",
        "scikit_learn": "sklearn",
        "opencv_python": "cv2",
        "pillow": "pil",
        "beautifulsoup4": "bs4",
    }
    result = set()
    for dependency in _string_list(dependencies):
        base = re.split(r"[<>=!~\[\s]", dependency, maxsplit=1)[0].strip()
        if base:
            normalized = base.replace("-", "_").casefold()
            result.add(normalized)
            if normalized in package_import_aliases:
                result.add(package_import_aliases[normalized])
    return result


def validate_conversation_skill_draft(draft, allowed_source_ids=None):
    normalized = normalize_skill_draft(
        draft,
        fallback_title=_string((draft or {}).get("description")),
        mode=_string((draft or {}).get("mode"), "create"),
    )
    allowed_ids = set(_string_list(allowed_source_ids or normalized.get("source_message_ids")))
    issues = []

    def add(severity, code, message, resource_path=""):
        issues.append(DraftValidationIssue(severity, code, message, resource_path))

    if normalized.get("mode") == "create" and not is_valid_skill_name(normalized.get("skill_name")):
        add("error", "invalid_skill_name", "Skill 名称只能包含英文字母、数字和连字符。")
    if normalized.get("mode") == "create" and not normalized.get("description"):
        add("error", "missing_description", "Skill 说明不能为空。")
    if not normalized.get("instructions_md") and not normalized.get("usage_guidelines") and not normalized.get("experience_items"):
        add("error", "missing_instructions", "Skill 必须包含指导正文或可追加经验。")

    text_fields = [
        normalized.get("description"),
        normalized.get("description_cn"),
        normalized.get("instructions_md"),
        normalized.get("usage_guidelines"),
        "\n".join(normalized.get("workflow") or []),
        "\n".join(normalized.get("experience_items") or []),
        normalized.get("impl_py"),
    ]
    combined_text = "\n".join(str(item or "") for item in text_fields)
    if SECRET_ASSIGNMENT_RE.search(combined_text) or BEARER_TOKEN_RE.search(combined_text):
        add("error", "secret_literal", "草稿包含疑似密钥、令牌或密码字面值。")
    if WINDOWS_USER_PATH_RE.search(combined_text) or UNIX_USER_PATH_RE.search(combined_text):
        add("error", "user_path_literal", "草稿包含真实 Windows 用户目录，请改为参数或占位符。")
    elif WINDOWS_ABSOLUTE_PATH_RE.search(combined_text) or WINDOWS_FORWARD_PATH_RE.search(combined_text):
        add("warning", "absolute_path_literal", "草稿包含绝对路径，请确认它是参数示例而不是一次性路径。")
    if len(normalized.get("instructions_md") or "") > 25_000:
        add("warning", "instructions_too_long", "Skill 正文过长，建议将详细资料移到 references/。")

    ok, error = validate_impl_py(normalized.get("impl_py"))
    if not ok:
        add("error", "invalid_impl", error, "impl.py")
    impl_refs = set(extract_impl_tool_refs(normalized.get("impl_py")))
    declared_refs = set(_string_list(normalized.get("tool_refs")))
    missing_refs = sorted(declared_refs - impl_refs) if normalized.get("impl_py") else []
    if missing_refs:
        add(
            "error",
            "missing_tool_function",
            "impl.py 未定义已声明工具：" + ", ".join(missing_refs),
            "impl.py",
        )
    import_roots = _python_import_roots(normalized.get("impl_py"))
    import_sources = {
        name: {"impl.py"} for name in import_roots
    }

    raw_resources = _dict_list(normalized.get("resources"))
    normalized_resources = normalize_draft_resources(raw_resources)
    normalized_paths = {item["path"] for item in normalized_resources}
    for item in raw_resources:
        path = _string(item.get("path")).replace("\\", "/").strip("/")
        if path not in normalized_paths:
            add("error", "invalid_resource_path", f"资源路径无效：{path or '(空)'}", path)
            continue
        refs = _source_refs(item.get("source_message_ids"))
        invalid = [ref for ref in refs if allowed_ids and ref not in allowed_ids]
        if invalid:
            add(
                "error",
                "invalid_resource_source_ref",
                "资源引用了未选择的会话消息：" + ", ".join(invalid),
                path,
            )
        content = str(item.get("content") or "")
        if SECRET_ASSIGNMENT_RE.search(content) or BEARER_TOKEN_RE.search(content):
            add("error", "resource_secret_literal", "资源包含疑似密钥或令牌。", path)
        if WINDOWS_USER_PATH_RE.search(content) or UNIX_USER_PATH_RE.search(content):
            add("error", "resource_user_path", "资源包含真实 Windows 用户目录。", path)
        if item.get("kind") == "script" and path.endswith(".py"):
            resource_ok, resource_error = validate_impl_py(content)
            if not resource_ok:
                add("error", "invalid_script", resource_error, path)
            resource_imports = _python_import_roots(content)
            import_roots.update(resource_imports)
            for name in resource_imports:
                import_sources.setdefault(name, set()).add(path)

    stdlib_names = set(getattr(sys, "stdlib_module_names", set()))
    external_imports = {
        name for name in import_roots
        if name and name not in stdlib_names and name not in {"core", "ui"}
    }
    declared_dependencies = _declared_python_import_names(normalized.get("python_dependencies"))
    undeclared = sorted(
        name for name in external_imports
        if name.casefold() not in declared_dependencies
    )
    for name in undeclared:
        paths = sorted(import_sources.get(name) or [])
        if len(paths) == 1:
            add(
                "error",
                "undeclared_python_dependency",
                f"Python 依赖未声明：{name}",
                paths[0],
            )
        else:
            add(
                "error",
                "undeclared_python_dependency",
                f"Python 依赖未声明：{name}（{', '.join(paths)}）",
            )

    if normalized.get("quality") == "low":
        add("warning", "low_confidence", "会话证据不足，保存前必须确认缺失项。")
    if normalized.get("missing_evidence"):
        add("warning", "missing_evidence", "草稿仍有未补足的证据。")
    if not normalized.get("anti_triggers"):
        add("warning", "missing_anti_triggers", "未声明不适用场景，可能导致 Skill 误触发。")

    return DraftValidationResult(
        ok=not any(item.severity == "error" for item in issues),
        issues=issues,
    ).to_dict()


class ConversationSkillCaptureRepository:
    def __init__(self, root_dir=None):
        self.root_dir = os.path.abspath(
            root_dir
            or os.path.join(get_app_data_dir(), "conversation_skill_drafts")
        )

    def _path(self, capture_id):
        normalized = str(capture_id or "").strip()
        if not re.match(r"^[A-Za-z0-9-]+$", normalized):
            raise ValueError("Invalid capture_id.")
        return os.path.join(self.root_dir, f"{normalized}.json")

    def create(self, session_id, source_message_ids):
        capture = {
            "version": 1,
            "capture_id": str(uuid.uuid4()),
            "session_id": _string(session_id),
            "source_message_ids": _string_list(source_message_ids),
            "phase": "analyzing",
            "evidence": {},
            "destination": {},
            "draft": {},
            "validation": {},
            "error": "",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self.save(capture)
        return capture

    def save(self, capture):
        payload = copy.deepcopy(capture if isinstance(capture, dict) else {})
        capture_id = _string(payload.get("capture_id"))
        if not capture_id:
            raise ValueError("capture_id is required.")
        payload["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        os.makedirs(self.root_dir, exist_ok=True)
        _atomic_write_json(self._path(capture_id), payload)
        return payload

    def load(self, capture_id):
        path = self._path(capture_id)
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else None

    def list_for_session(self, session_id, include_saved=False):
        if not os.path.isdir(self.root_dir):
            return []
        results = []
        for filename in sorted(os.listdir(self.root_dir)):
            if not filename.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.root_dir, filename), "r", encoding="utf-8-sig") as handle:
                    payload = json.load(handle)
            except Exception:
                continue
            if not isinstance(payload, dict) or _string(payload.get("session_id")) != _string(session_id):
                continue
            if not include_saved and payload.get("phase") in {"saved", "discarded"}:
                continue
            results.append(payload)
        results.sort(key=lambda item: _string(item.get("updated_at")), reverse=True)
        return results

    def discard(self, capture_id):
        capture = self.load(capture_id)
        if not capture:
            return False
        capture["phase"] = "discarded"
        capture["evidence"] = {}
        capture["destination"] = {}
        capture["target_snapshot"] = {}
        capture["draft"] = {}
        capture["validation"] = {}
        self.save(capture)
        return True

    def mark_saved(self, capture_id, skill_name):
        capture = self.load(capture_id)
        if not capture:
            return False
        capture["phase"] = "saved"
        capture["saved_skill_name"] = _string(skill_name)
        capture["evidence"] = {}
        capture["destination"] = {}
        capture["target_snapshot"] = {}
        capture["draft"] = {}
        capture["validation"] = {}
        self.save(capture)
        return True


def merge_script_assets_metadata(skill_record, draft):
    if not skill_record:
        return SaveResult(False, "Target skill not found.")
    skill_path = skill_record.get("path") or ""
    json_path = os.path.join(skill_path, "skill.json")
    assets = write_script_assets(skill_path, draft)
    if not assets:
        return SaveResult(True, "No script assets selected.", skill_path)
    payload = {}
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8-sig") as handle:
                parsed = json.load(handle)
            payload = parsed if isinstance(parsed, dict) else {}
        except Exception:
            payload = {}
    script_refs = _string_list(payload.get("script_refs"))
    script_entries = _dict_list(payload.get("script_entries"))
    existing_paths = {str(item.get("path") or "").replace("\\", "/").lower() for item in script_entries}
    for item in assets:
        if item["path"] not in script_refs:
            script_refs.append(item["path"])
        if item["path"].lower() not in existing_paths:
            script_entries.append(
                {
                    "name": item["name"],
                    "path": item["path"],
                    "runtime": "python",
                    "description": item.get("description") or "",
                }
            )
            existing_paths.add(item["path"].lower())
    payload.setdefault("version", 2)
    payload.setdefault("name", skill_record.get("name") or os.path.basename(skill_path))
    payload["script_refs"] = script_refs
    payload["script_entries"] = script_entries
    try:
        _atomic_write_json(json_path, payload)
        return SaveResult(True, "Skill script assets updated.", skill_path)
    except Exception as exc:
        return SaveResult(False, f"Failed to update skill script metadata: {exc}", json_path)


def save_new_skill(draft, target_root=None):
    draft = normalize_skill_draft(draft)
    skill_name = draft["skill_name"]
    if not is_valid_skill_name(skill_name):
        return SaveResult(False, "Skill name must contain only letters, numbers, and hyphens.")

    validation = validate_conversation_skill_draft(
        draft,
        allowed_source_ids=draft.get("source_message_ids"),
    )
    if not validation.get("ok"):
        messages = [
            item.get("message") for item in validation.get("issues") or []
            if item.get("severity") == "error"
        ]
        return SaveResult(False, "Skill validation failed: " + "; ".join(messages))

    impl_py = _string(draft.get("impl_py"))
    ok, error = validate_impl_py(impl_py)
    if not ok:
        return SaveResult(False, error)

    root = target_root or os.path.join(get_app_data_dir(), "ai_skills")
    target_dir = os.path.join(root, skill_name)
    if os.path.exists(target_dir):
        return SaveResult(False, f"Skill '{skill_name}' already exists.", target_dir)

    staging_dir = ""
    try:
        os.makedirs(root, exist_ok=True)
        staging_dir = tempfile.mkdtemp(prefix=f".{skill_name}-staging-", dir=root)
        os.makedirs(os.path.join(staging_dir, "experience"), exist_ok=False)
        _atomic_write_text(os.path.join(staging_dir, "SKILL.md"), build_skill_md(draft))
        _atomic_write_json(os.path.join(staging_dir, "skill.json"), build_skill_json(draft))
        if impl_py:
            _atomic_write_text(os.path.join(staging_dir, "impl.py"), impl_py.rstrip() + "\n")
        write_script_assets(staging_dir, draft)
        write_draft_resources(staging_dir, draft)
        os.replace(staging_dir, target_dir)
        staging_dir = ""
        return SaveResult(True, f"Skill '{skill_name}' created.", target_dir)
    except Exception as exc:
        return SaveResult(False, f"Failed to create skill: {exc}", target_dir)
    finally:
        if staging_dir:
            shutil.rmtree(staging_dir, ignore_errors=True)


def merge_skill_json_metadata(skill_record, tags=None, triggers=None, anti_triggers=None):
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
    for key, additions in (
        ("tags", tags),
        ("triggers", triggers),
        ("anti_triggers", anti_triggers),
    ):
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


def merge_draft_resources_metadata(skill_record, draft):
    if not skill_record:
        return SaveResult(False, "Target skill not found.")
    resources = write_draft_resources(skill_record.get("path") or "", draft)
    if not resources:
        return SaveResult(True, "No draft resources selected.", skill_record.get("path") or "")
    json_path = os.path.join(skill_record.get("path") or "", "skill.json")
    payload = {}
    if os.path.isfile(json_path):
        with open(json_path, "r", encoding="utf-8-sig") as handle:
            parsed = json.load(handle)
        payload = parsed if isinstance(parsed, dict) else {}
    references = _string_list(payload.get("references"))
    script_refs = _string_list(payload.get("script_refs"))
    script_entries = _dict_list(payload.get("script_entries"))
    known_entries = {
        _string(item.get("path")).replace("\\", "/").lower()
        for item in script_entries
    }
    for item in resources:
        if item["kind"] == "reference":
            if item["path"] not in references:
                references.append(item["path"])
            continue
        if item["path"] not in script_refs:
            script_refs.append(item["path"])
        if item["path"].lower() not in known_entries:
            script_entries.append(
                {
                    "name": _slugify(
                        os.path.splitext(os.path.basename(item["path"]))[0],
                        default="conversation-script",
                    ).replace("-", "_"),
                    "path": item["path"],
                    "runtime": "python" if item["path"].endswith(".py") else "shell",
                    "description": item.get("description") or "",
                }
            )
            known_entries.add(item["path"].lower())
    payload.setdefault("version", 2)
    payload.setdefault("name", skill_record.get("name") or os.path.basename(skill_record.get("path") or ""))
    payload["references"] = references
    payload["script_refs"] = script_refs
    payload["script_entries"] = script_entries
    _atomic_write_json(json_path, payload)
    return SaveResult(True, "Skill resources updated.", skill_record.get("path") or "")


def _update_existing_skill_from_draft_in_place(skill_manager, skill_name, draft, strategy="append"):
    if not skill_manager:
        return SaveResult(False, "SkillManager is not available.")
    if not skill_name:
        return SaveResult(False, "Target skill is required.")
    record = getattr(skill_manager, "skill_records", {}).get(skill_name)
    if not record:
        return SaveResult(False, f"Skill '{skill_name}' not found.")

    draft = normalize_skill_draft(draft, fallback_title=skill_name, mode="update")
    strategy = (strategy or "append_experience").strip().lower()
    if strategy == "rewrite":
        strategy = "merge_guidance"
    if strategy in {"merge_guidance", "merge"}:
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
        existing_texts = {
            re.sub(r"\s+", " ", _string(entry.get("experience_text"))).casefold()
            for entry in (record.get("experience_entries") or [])
            if isinstance(entry, dict) and _string(entry.get("experience_text"))
        }
        for item in items:
            normalized_item = re.sub(r"\s+", " ", item).casefold()
            if normalized_item in existing_texts:
                continue
            success, message = skill_manager.record_experience(
                experience_text=item,
                skill_name=skill_name,
                tags=_string_list(draft.get("tags")),
                source="conversation_skill",
            )
            if not success:
                return SaveResult(False, message, record.get("path") or "")
            existing_texts.add(normalized_item)

    metadata_result = merge_skill_json_metadata(
        record,
        tags=draft.get("tags"),
        triggers=draft.get("triggers"),
        anti_triggers=draft.get("anti_triggers"),
    )
    if not metadata_result.ok:
        return metadata_result
    script_result = merge_script_assets_metadata(record, draft)
    if not script_result.ok:
        return script_result
    resource_result = merge_draft_resources_metadata(record, draft)
    if not resource_result.ok:
        return resource_result
    return SaveResult(True, f"Skill '{skill_name}' updated.", record.get("path") or "")


def update_existing_skill_from_draft(skill_manager, skill_name, draft, strategy="append"):
    if not skill_manager or not skill_name:
        return _update_existing_skill_from_draft_in_place(skill_manager, skill_name, draft, strategy=strategy)
    record = getattr(skill_manager, "skill_records", {}).get(skill_name)
    target_path = os.path.abspath((record or {}).get("path") or "")
    if not target_path or not os.path.isdir(target_path):
        return SaveResult(False, f"Skill '{skill_name}' not found.")
    expected_revision = _string((draft or {}).get("target_revision"))
    current_revision = compute_skill_revision(target_path)
    if expected_revision and expected_revision != current_revision:
        return SaveResult(
            False,
            "Target Skill changed after compilation. Recompile the draft before saving.",
            target_path,
        )
    validation = validate_conversation_skill_draft(
        {**(draft or {}), "mode": "update"},
        allowed_source_ids=(draft or {}).get("source_message_ids"),
    )
    if not validation.get("ok"):
        messages = [
            item.get("message") for item in validation.get("issues") or []
            if item.get("severity") == "error"
        ]
        return SaveResult(False, "Skill validation failed: " + "; ".join(messages), target_path)
    parent_dir = os.path.dirname(target_path)
    transaction_root = tempfile.mkdtemp(prefix=f".{skill_name}-transaction-", dir=parent_dir)
    staged_path = os.path.join(transaction_root, skill_name)
    backup_path = ""
    try:
        shutil.copytree(target_path, staged_path)
        staged_manager = copy.copy(skill_manager)
        staged_manager.skills_dirs = [transaction_root]
        staged_manager.skill_records = dict(skill_manager.skill_records)
        staged_record = copy.deepcopy(record)
        staged_record["path"] = staged_path
        staged_manager.skill_records[skill_name] = staged_record
        result = _update_existing_skill_from_draft_in_place(
            staged_manager,
            skill_name,
            draft,
            strategy=strategy,
        )
        if not result.ok:
            return result
        validation = staged_manager.validate_skill(skill_name)
        if not validation.get("ok"):
            return SaveResult(False, "Skill validation failed: " + "; ".join(validation.get("issues") or []), target_path)
        backup_path = tempfile.mkdtemp(prefix=f".{skill_name}-backup-", dir=parent_dir)
        os.rmdir(backup_path)
        os.replace(target_path, backup_path)
        try:
            os.replace(staged_path, target_path)
        except Exception:
            os.replace(backup_path, target_path)
            backup_path = ""
            raise
        shutil.rmtree(backup_path, ignore_errors=True)
        backup_path = ""
        try:
            refreshed_record = skill_manager._load_skill_record(skill_name, target_path)
        except Exception:
            refreshed_record = copy.deepcopy(staged_manager.skill_records.get(skill_name) or staged_record)
        refreshed_record["path"] = target_path
        skill_manager.skill_records[skill_name] = refreshed_record
        return SaveResult(True, f"Skill '{skill_name}' updated.", target_path)
    except Exception as exc:
        return SaveResult(False, f"Failed to update skill atomically: {exc}", target_path)
    finally:
        if backup_path:
            shutil.rmtree(backup_path, ignore_errors=True)
        shutil.rmtree(transaction_root, ignore_errors=True)
