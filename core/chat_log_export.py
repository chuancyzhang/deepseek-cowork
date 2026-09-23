"""Readable, read-only export of a conversation snapshot."""

import json
import re
from datetime import datetime

from core.conversation_render import (
    is_ppt_agent_internal_stage_message,
    project_visible_messages,
)


def chat_log_filename(title, captured_at):
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(title)).strip(" .")[:60]
    return f"聊天记录-{title or '新对话'}-{captured_at:%Y%m%d-%H%M%S}.log"


def _text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _time(value):
    if not value:
        return "时间未记录"
    try:
        return datetime.fromtimestamp(float(value)).astimezone().isoformat(timespec="seconds")
    except (ValueError, TypeError, OverflowError, OSError):
        return str(value)


def iter_chat_log(snapshot):
    """Yield text sections without changing ledger rows or their metadata."""
    yield (
        "DeepSeek Cowork 聊天记录\n"
        f"对话：{snapshot['title']}\n会话 ID：{snapshot['session_id']}\n"
        f"快照时间：{snapshot['captured_at'].isoformat(timespec='seconds')}\n"
        f"状态：{'运行中（仅包含快照时已有内容）' if snapshot['running'] else '未在运行'}\n"
        "范围：当前对话的消息、思考过程、已记录的工具调用与返回。\n"
        "附件仅列出引用，不打包文件；不包含系统提示词、隐藏上下文或子 Agent 独立会话。\n"
    )
    messages = project_visible_messages(snapshot["messages"], timeline_events=snapshot["timeline"])
    stage_keys = set()
    tool_ids = set()
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role not in {"user", "assistant", "tool"} or is_ppt_agent_internal_stage_message(message):
            continue
        meta = message.get("meta") or {}
        if role == "assistant":
            stage_keys.add((
                str(meta.get("ui_turn_id") or meta.get("turn_id") or ""),
                str(meta.get("ui_turn_group_id") or ""),
                str(meta.get("ui_stage_id") or ""),
            ))
        label = {"user": "用户", "assistant": "AI", "tool": "工具返回"}[role]
        if meta.get("same_turn_guidance"):
            label = "用户补充"
        yield f"\n{'=' * 64}\n[{label}] {_time(message.get('created_at'))}\n"
        if role == "tool":
            tool_id = str(message.get("tool_call_id") or "")
            tool_ids.add(tool_id)
            yield f"调用 ID：{tool_id}\n"
        reasoning = message.get("reasoning_content") or message.get("reasoning")
        if reasoning:
            yield f"\n[思考过程]\n{_text(reasoning)}\n\n[正文]\n"
        content = meta.get("display_content") if role == "user" else None
        if content is None:
            content = message.get("content")
        if content:
            yield _text(content) + "\n"
        if role == "tool" and not content and message.get("result_obj") is not None:
            yield _text(message["result_obj"]) + "\n"
        attachments = list(meta.get("user_added_files") or [])
        for part in message.get("content_parts") or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text" and not content and part.get("text"):
                yield str(part["text"]) + "\n"
            elif part.get("type") in {"input_file", "input_image", "output_image", "input_audio"}:
                reference = part.get("path") or part.get("name") or "图片 / 附件（无本地路径）"
                if reference not in attachments:
                    attachments.append(reference)
        for attachment in attachments:
            yield f"附件：{_text(attachment)}\n"
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            function = call.get("function") or {}
            yield (
                f"\n[工具调用] {function.get('name') or '未命名工具'}\n"
                f"调用 ID：{call.get('id') or ''}\n参数：\n{_text(function.get('arguments'))}\n"
            )
        if meta.get("context_visible_interruption"):
            yield "[此条回复未完成，已保留当时内容]\n"

    # Live events not yet present in the ledger stay in a clearly labelled appendix.
    # Explicit stage/tool identities avoid repeating committed visible fragments.
    pending = []
    if snapshot["running"]:
        for event in snapshot["timeline"]:
            if not isinstance(event, dict):
                continue
            if str(event.get("turn_id") or "") != snapshot["turn_id"]:
                continue
            kind = event.get("kind")
            key = tuple(str(event.get(field) or "") for field in ("turn_id", "group_id", "stage_id"))
            if kind in {"thinking", "content_fragment", "final_content"} and key not in stage_keys:
                if event.get("text"):
                    label = "思考过程" if kind == "thinking" else "AI 正文"
                    pending.append(f"[{label}] {_time(event.get('started_at'))}\n{event['text']}\n")
            elif kind == "tool" and str(event.get("tool_call_id") or "") not in tool_ids:
                tool = snapshot["live_tools"].get(str(event.get("tool_call_id") or ""), {})
                status = {
                    "running": "运行中", "completed": "已完成", "failed": "失败",
                }.get(event.get("status"), event.get("status") or "未记录")
                pending.append(
                    f"[工具] {event.get('tool_name') or event.get('text') or ''}\n"
                    f"调用 ID：{event.get('tool_call_id') or ''}\n状态：{status}\n"
                    f"参数：\n{_text(tool.get('args'))}\n返回：\n{_text(tool.get('result'))}\n"
                )
            elif kind == "error" and event.get("text"):
                pending.append(f"[运行提示]\n{event['text']}\n")
    if pending:
        yield f"\n{'=' * 64}\n[当前运行的未归档过程快照]\n\n"
        yield "\n".join(pending)
    yield "\n—— 聊天记录结束 ——\n"
