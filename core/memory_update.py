import math
import os
import tempfile
import time


DEFAULT_MEMORY_BATCH_TOKEN_LIMIT = 200_000


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
    max_tokens = max(1, int(max_tokens or DEFAULT_MEMORY_BATCH_TOKEN_LIMIT))
    batches = []
    current_parts = []
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
                batches.append("\n\n---\n\n".join(current_parts))
                current_parts = []
                current_tokens = 0
            current_parts.append(part_text)
            current_tokens += token_count

    if current_parts:
        batches.append("\n\n---\n\n".join(current_parts))
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


def collect_llm_content(provider, messages):
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
        summaries.append(collect_llm_content(provider, messages))

    if progress_callback:
        progress_callback("正在合并长期记忆预览")
    final_messages = build_memory_update_messages(
        current_memory=current_memory,
        batch_summaries=summaries,
    )
    final_content = collect_llm_content(provider, final_messages)
    return {
        "content": final_content,
        "batch_count": len(batches),
        "transcript_count": transcript_count,
        "estimated_tokens": estimated_tokens,
        "batch_summaries": summaries,
    }


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
