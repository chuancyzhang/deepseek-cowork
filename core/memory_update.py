import json
import math
import os
import tempfile
import time

from core.memory_store import normalize_workspace_dir, workspace_key


DEFAULT_MEMORY_BATCH_TOKEN_LIMIT = 200_000
MEMORY_UPDATE_STATE_FILENAME = "memories_update_state.json"


def estimate_tokens(text):
    text = str(text or "")
    if not text:
        return 0
    non_ascii = 0
    ascii_nonspace = 0
    for char in text:
        if char.isspace():
            continue
        if ord(char) > 127:
            non_ascii += 1
        else:
            ascii_nonspace += 1
    return non_ascii + int(math.ceil(ascii_nonspace / 4.0))


def render_transcript(transcript):
    title = transcript.get("title") or "未命名会话"
    conversation_id = transcript.get("id") or ""
    updated_at = transcript.get("updated_at_iso") or ""
    created_at = transcript.get("created_at_iso") or ""
    lines = [
        f"# 会话: {title}",
        f"- ID: {conversation_id}",
    ]
    if created_at:
        lines.append(f"- 创建时间: {created_at}")
    if updated_at:
        lines.append(f"- 更新时间: {updated_at}")
    if transcript.get("source"):
        lines.append(f"- 来源: {transcript.get('source')}")
    lines.append("")
    for message in transcript.get("messages") or []:
        role = message.get("role") or "unknown"
        created = message.get("created_at_iso") or ""
        prefix = f"## {role}"
        if created:
            prefix += f" ({created})"
        lines.append(prefix)
        content = str(message.get("content") or "").strip()
        reasoning = str(message.get("reasoning_content") or "").strip()
        if content:
            lines.append(content)
        if reasoning:
            lines.append("[reasoning]")
            lines.append(reasoning)
        if message.get("tool_calls"):
            lines.append("[tool_calls]")
            lines.append(str(message.get("tool_calls")))
        if message.get("tool_call_id"):
            lines.append(f"[tool_call_id] {message.get('tool_call_id')}")
        lines.append("")
    return "\n".join(lines).strip()


def _split_large_text(text, max_tokens):
    text = str(text or "")
    if estimate_tokens(text) <= max_tokens:
        return [text]
    chunks = []
    remaining = text
    # Character count is a safe upper bound for mixed Chinese/English text under
    # the estimator above, then tighten by shrinking until it fits.
    rough_size = max(1, max_tokens)
    while remaining:
        candidate = remaining[:rough_size]
        while candidate and estimate_tokens(candidate) > max_tokens:
            candidate = candidate[: max(1, int(len(candidate) * 0.8))]
        if not candidate:
            candidate = remaining[:1]
        chunks.append(candidate)
        remaining = remaining[len(candidate):]
    return chunks


def batch_transcripts(transcripts, max_tokens=DEFAULT_MEMORY_BATCH_TOKEN_LIMIT):
    return [batch["text"] for batch in build_transcript_batches(transcripts, max_tokens)]


def build_transcript_batches(transcripts, max_tokens=DEFAULT_MEMORY_BATCH_TOKEN_LIMIT):
    max_tokens = max(1, int(max_tokens or DEFAULT_MEMORY_BATCH_TOKEN_LIMIT))
    batches = []
    current_parts = []
    current_transcripts = []
    current_tokens = 0

    for transcript in transcripts or []:
        text = transcript if isinstance(transcript, str) else render_transcript(transcript)
        if not text.strip():
            continue
        title = transcript.get("title") if isinstance(transcript, dict) else "大型会话"
        if estimate_tokens(text) > max_tokens:
            header_template = f"# 会话分片: {title} ({{}}/{{}})\n\n"
            reserve = estimate_tokens(header_template.format(999, 999))
            part_limit = max(1, max_tokens - reserve)
            parts = _split_large_text(text, part_limit)
        else:
            header_template = ""
            parts = [text]
        for part_index, part in enumerate(parts, start=1):
            part_text = part
            if header_template:
                part_text = header_template.format(part_index, len(parts)) + part
            token_count = estimate_tokens(part_text)
            if current_parts and current_tokens + token_count > max_tokens:
                batches.append(
                    {
                        "text": "\n\n---\n\n".join(current_parts),
                        "transcripts": current_transcripts,
                        "estimated_tokens": current_tokens,
                    }
                )
                current_parts = []
                current_transcripts = []
                current_tokens = 0
            current_parts.append(part_text)
            if isinstance(transcript, dict) and transcript not in current_transcripts:
                current_transcripts.append(transcript)
            current_tokens += token_count

    if current_parts:
        batches.append(
            {
                "text": "\n\n---\n\n".join(current_parts),
                "transcripts": current_transcripts,
                "estimated_tokens": current_tokens,
            }
        )
    return batches


def build_memory_update_messages(current_memory, batch_text=None, batch_index=1, batch_count=1, batch_summaries=None):
    system_prompt = (
        "你是长期记忆整理助手。只保留长期稳定、有复用价值的信息：用户偏好、项目背景、"
        "持续约定、重要环境事实、反复出现的工作方式。不要写入一次性任务细节、敏感信息、"
        "临时状态、完整聊天记录或冗余内容。输出 Markdown。"
    )
    if batch_summaries is not None:
        user_prompt = (
            "请将当前长期记忆与下面的分批摘要合并为最终 memories.md。要求：\n"
            "- 去重、纠错、合并相近条目；\n"
            "- 使用清晰的 Markdown 小节；\n"
            "- 内容应是长期记录，不要描述你的整理过程；\n"
            "- 如果没有值得保存的信息，输出一个简短的空状态说明。\n\n"
            f"# 当前 memories.md\n{current_memory or '(空)'}\n\n"
            f"# 历史分批摘要\n{chr(10).join(batch_summaries) or '(无)'}"
        )
    else:
        user_prompt = (
            f"下面是第 {batch_index}/{batch_count} 批历史会话。请提炼这一批中值得进入长期记忆的内容。"
            "输出结构化 Markdown，避免重复和临时细节。\n\n"
            f"# 当前 memories.md\n{current_memory or '(空)'}\n\n"
            f"# 历史会话批次\n{batch_text or ''}"
        )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_incremental_memory_update_messages(current_memory, batch_text, batch_index=1, batch_count=1):
    system_prompt = (
        "你是长期记忆整理助手。你需要维护完整的 memories.md。只保留长期稳定、有复用价值的信息："
        "用户偏好、项目背景、持续约定、重要环境事实、反复出现的工作方式。不要写入一次性任务细节、"
        "敏感信息、临时状态、完整聊天记录或冗余内容。输出完整 Markdown 文件内容。"
    )
    user_prompt = (
        f"下面是第 {batch_index}/{batch_count} 批历史会话。请将这一批值得长期保存的信息合并进当前 memories.md，"
        "并输出合并后的完整 memories.md。要求：\n"
        "- 保留已有仍然有效的长期记忆；\n"
        "- 去重、纠错、合并相近条目；\n"
        "- 不要输出说明文字、过程记录或代码块围栏；\n"
        "- 如果这一批没有新长期信息，也请输出整理后的完整 memories.md。\n\n"
        f"# 当前 memories.md\n{current_memory or '(空)'}\n\n"
        f"# 历史会话批次\n{batch_text or ''}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def collect_llm_content(provider, messages, max_retries=5, progress_callback=None):
    max_retries = max(1, int(max_retries or 1))
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            return _collect_llm_content_once(provider, messages)
        except RuntimeError as exc:
            last_error = exc
            if attempt >= max_retries:
                break
            if progress_callback:
                progress_callback(f"LLM 返回为空或失败，正在重试 {attempt + 1}/{max_retries}：{exc}")
            time.sleep(min(2.0, 0.3 * attempt))
    raise RuntimeError(str(last_error or "LLM did not return memory content."))


def _collect_llm_content_once(provider, messages):
    parts = []
    errors = []
    for chunk in provider.chat_stream(messages, tools=None):
        chunk_type = chunk.get("type") if isinstance(chunk, dict) else ""
        if chunk_type == "content":
            parts.append(chunk.get("content") or "")
        elif chunk_type == "error":
            errors.append(chunk.get("content") or "Unknown LLM error")
    if errors:
        raise RuntimeError("; ".join(errors))
    content = "".join(parts).strip()
    if not content:
        raise RuntimeError("LLM did not return memory content.")
    return content


def generate_memory_update(provider, current_memory, transcripts, max_batch_tokens=DEFAULT_MEMORY_BATCH_TOKEN_LIMIT, progress_callback=None):
    transcript_count = len(transcripts or [])
    batches = batch_transcripts(transcripts, max_batch_tokens)
    if not batches:
        return {
            "content": "",
            "batch_count": 0,
            "transcript_count": transcript_count,
            "estimated_tokens": 0,
            "batch_summaries": [],
        }

    estimated_tokens = sum(estimate_tokens(batch) for batch in batches)
    summaries = []
    for index, batch in enumerate(batches, start=1):
        if progress_callback:
            progress_callback(f"正在提炼历史批次 {index}/{len(batches)}")
        messages = build_memory_update_messages(
            current_memory=current_memory,
            batch_text=batch,
            batch_index=index,
            batch_count=len(batches),
        )
        summaries.append(collect_llm_content(provider, messages, progress_callback=progress_callback))

    if progress_callback:
        progress_callback("正在合并长期记忆预览")
    final_messages = build_memory_update_messages(
        current_memory=current_memory,
        batch_summaries=summaries,
    )
    final_content = collect_llm_content(provider, final_messages, progress_callback=progress_callback)
    return {
        "content": final_content,
        "batch_count": len(batches),
        "transcript_count": transcript_count,
        "estimated_tokens": estimated_tokens,
        "batch_summaries": summaries,
    }


def generate_memory_update_incremental(
    provider,
    current_memory,
    transcripts,
    history_dir,
    max_batch_tokens=DEFAULT_MEMORY_BATCH_TOKEN_LIMIT,
    progress_callback=None,
    preview_callback=None,
    state_callback=None,
):
    transcript_count = len(transcripts or [])
    batches = build_transcript_batches(transcripts, max_batch_tokens)
    if not batches:
        return {
            "content": current_memory or "",
            "batch_count": 0,
            "transcript_count": transcript_count,
            "estimated_tokens": 0,
            "processed_transcripts": [],
        }

    estimated_tokens = sum(batch.get("estimated_tokens") or 0 for batch in batches)
    updated_memory = current_memory or ""
    processed_transcripts = []
    for index, batch in enumerate(batches, start=1):
        if progress_callback:
            progress_callback(f"正在合并并保存历史批次 {index}/{len(batches)}")
        messages = build_incremental_memory_update_messages(
            current_memory=updated_memory,
            batch_text=batch.get("text") or "",
            batch_index=index,
            batch_count=len(batches),
        )
        updated_memory = collect_llm_content(provider, messages, progress_callback=progress_callback)
        save_result = save_memory_file_with_backup(history_dir, updated_memory + "\n")
        batch_transcripts = batch.get("transcripts") or []
        processed_transcripts.extend(batch_transcripts)
        processed_at = max([transcript_updated_at(item) for item in processed_transcripts] or [0])
        state = save_memory_update_state(history_dir, processed_at, processed_transcripts)
        payload = {
            "content": updated_memory,
            "batch_index": index,
            "batch_count": len(batches),
            "transcript_count": transcript_count,
            "processed_count": len(processed_transcripts),
            "estimated_tokens": estimated_tokens,
            "save_result": save_result,
            "state": state,
            "processed_at": processed_at,
        }
        if preview_callback:
            preview_callback(payload)
        if state_callback:
            state_callback(state)

    if progress_callback:
        progress_callback("长期记忆已按批次更新完成")
    return {
        "content": updated_memory,
        "batch_count": len(batches),
        "transcript_count": transcript_count,
        "estimated_tokens": estimated_tokens,
        "processed_transcripts": processed_transcripts,
    }


def memory_update_state_path(history_dir):
    return os.path.join(history_dir, MEMORY_UPDATE_STATE_FILENAME)


def load_memory_update_state(history_dir):
    path = memory_update_state_path(history_dir)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"schema_version": 2, "global": {}, "workspaces": {}}
        if data.get("schema_version") == 2:
            data.setdefault("global", {})
            data.setdefault("workspaces", {})
            return data
        # The former flat cursor represented the all-history/global flow.
        return {"schema_version": 2, "global": data, "workspaces": {}}
    except Exception:
        return {"schema_version": 2, "global": {}, "workspaces": {}}


def memory_update_scope_state(history_dir, scope="global", workspace_dir=""):
    state = load_memory_update_state(history_dir)
    if scope == "global":
        return dict(state.get("global") or {})
    key = workspace_key(workspace_dir)
    if not key:
        raise ValueError("工作区记忆更新必须指定工作区路径。")
    return dict((state.get("workspaces") or {}).get(key) or {})


def transcript_updated_at(transcript):
    values = []
    for key in ("updated_at", "created_at"):
        try:
            value = int(transcript.get(key) or 0)
        except Exception:
            value = 0
        if value > 0:
            values.append(value)
    for message in transcript.get("messages") or []:
        try:
            value = int(message.get("created_at") or 0)
        except Exception:
            value = 0
        if value > 0:
            values.append(value)
    return max(values) if values else 0


def filter_transcripts_for_memory_update(transcripts, last_processed_at=0, cutoff_at=None):
    try:
        last_processed_at = int(last_processed_at or 0)
    except Exception:
        last_processed_at = 0
    try:
        cutoff_at = int(cutoff_at or time.time())
    except Exception:
        cutoff_at = int(time.time())

    filtered = []
    for transcript in transcripts or []:
        updated_at = transcript_updated_at(transcript)
        if updated_at and updated_at <= last_processed_at:
            continue
        if updated_at and updated_at > cutoff_at:
            continue
        filtered.append(transcript)
    return sorted(filtered, key=lambda item: (transcript_updated_at(item), item.get("id") or ""))


def filter_transcripts_for_workspace(transcripts, workspace_dir):
    target = normalize_workspace_dir(workspace_dir)
    if not target:
        raise ValueError("当前没有可用于生成摘要的工作区。")
    result = []
    for transcript in transcripts or []:
        meta = transcript.get("meta") if isinstance(transcript.get("meta"), dict) else {}
        candidate = normalize_workspace_dir(meta.get("workspace_dir"))
        if candidate and candidate == target:
            result.append(transcript)
    return result


def processed_conversation_records(transcripts):
    records = []
    for transcript in transcripts or []:
        records.append(
            {
                "id": transcript.get("id") or "",
                "title": transcript.get("title") or "",
                "source": transcript.get("source") or "",
                "updated_at": transcript_updated_at(transcript),
                "updated_at_iso": transcript.get("updated_at_iso"),
                "message_count": len(transcript.get("messages") or []),
            }
        )
    return records


def save_memory_update_state(history_dir, cutoff_at, transcripts, scope="global", workspace_dir=""):
    os.makedirs(history_dir, exist_ok=True)
    try:
        cutoff_at = int(cutoff_at or time.time())
    except Exception:
        cutoff_at = int(time.time())
    payload = {
        "last_processed_at": cutoff_at,
        "last_processed_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(cutoff_at)),
        "updated_at": int(time.time()),
        "processed_conversations": processed_conversation_records(transcripts),
    }
    state = load_memory_update_state(history_dir)
    state["schema_version"] = 2
    if scope == "global":
        state["global"] = payload
    else:
        key = workspace_key(workspace_dir)
        if not key:
            raise ValueError("工作区记忆更新必须指定工作区路径。")
        payload["workspace_dir"] = normalize_workspace_dir(workspace_dir)
        state.setdefault("workspaces", {})[key] = payload
    path = memory_update_state_path(history_dir)
    fd, tmp_path = tempfile.mkstemp(prefix="memories_update_state.", suffix=".tmp", dir=history_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
    return payload


def memories_path_for_history_dir(history_dir):
    return os.path.join(history_dir, "memories.md")


def read_memory_file(history_dir):
    path = memories_path_for_history_dir(history_dir)
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def save_memory_file_with_backup(history_dir, content):
    os.makedirs(history_dir, exist_ok=True)
    path = memories_path_for_history_dir(history_dir)
    backup_path = ""
    if os.path.exists(path):
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        backup_path = f"{path}.bak.{timestamp}"
        if os.path.exists(backup_path):
            counter = 2
            while os.path.exists(f"{backup_path}.{counter}"):
                counter += 1
            backup_path = f"{backup_path}.{counter}"
        with open(path, "rb") as src, open(backup_path, "wb") as dst:
            dst.write(src.read())
    fd, tmp_path = tempfile.mkstemp(prefix="memories.", suffix=".tmp", dir=history_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content or "")
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
    return {"path": path, "backup_path": backup_path}
