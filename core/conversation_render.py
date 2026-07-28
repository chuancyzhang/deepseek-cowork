import json


def _safe_jsonable(value):
    try:
        json.dumps(value, ensure_ascii=False, sort_keys=True)
        return value
    except Exception:
        return str(value)


def _join_segments(segments):
    cleaned = []
    for segment in segments or []:
        text = str(segment or "")
        if not text:
            continue
        cleaned.append(text)
    return "\n\n".join(cleaned).strip()


def _normalize_tool_args(arguments):
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except Exception:
            return arguments
    return arguments


def _is_hidden_context_message(message):
    if not isinstance(message, dict):
        return False
    meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
    return (
        bool(meta.get("hidden"))
        and meta.get("kind") in {"skill_context", "skill_context_update"}
    ) or bool(meta.get("embedded_agent_result")) or is_legacy_skill_change_notice_message(message)


def is_legacy_skill_change_notice_message(message):
    if not isinstance(message, dict):
        return False
    meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
    return bool(meta.get("ui_only")) and (
        isinstance(meta.get("skill_change"), dict)
        or bool(str(meta.get("skill_change_event_id") or "").strip())
    )


def is_same_turn_guidance_message(message):
    if not isinstance(message, dict):
        return False
    meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
    return bool(meta.get("same_turn_guidance"))


OFFICE_WORKFLOW_MODES = {"office_html_first", "office_file_conversion"}


def _is_office_draft_request(message):
    if not isinstance(message, dict):
        return False
    meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
    return meta.get("workflow_mode") in OFFICE_WORKFLOW_MODES


def _new_assistant_group():
    return {
        "messages": [],
        "reasoning_segments": [],
        "content_segments": [],
        "content_parts": [],
        "tool_order": [],
        "tools_by_id": {},
    }


def _ensure_tool_entry(group, tool_call_id, fallback_name="unknown_tool", fallback_args=None):
    tool_call_id = tool_call_id or f"tool-{len(group['tool_order'])}"
    if tool_call_id in group["tools_by_id"]:
        return group["tools_by_id"][tool_call_id]
    tool_entry = {
        "id": tool_call_id,
        "name": fallback_name,
        "args": fallback_args if fallback_args is not None else {},
        "result": "",
        "result_obj": None,
        "meta": {},
    }
    group["tool_order"].append(tool_call_id)
    group["tools_by_id"][tool_call_id] = tool_entry
    return tool_entry


def _finalize_assistant_group(group):
    if not group:
        return None
    tool_calls = [group["tools_by_id"][tool_id] for tool_id in group["tool_order"]]
    content = _join_segments(group["content_segments"])
    reasoning = "".join(str(item or "") for item in group["reasoning_segments"]).strip()
    content_parts = list(group["content_parts"])
    source_messages = list(group["messages"])
    if not (content or reasoning or tool_calls or source_messages):
        return None
    return {
        "type": "assistant",
        "content": content,
        "reasoning": reasoning,
        "content_parts": content_parts,
        "tool_calls": tool_calls,
        "messages": source_messages,
    }


def build_conversation_render_items(messages):
    items = []
    assistant_group = None

    def flush_group():
        nonlocal assistant_group
        item = _finalize_assistant_group(assistant_group)
        if item:
            items.append(item)
        assistant_group = None

    for raw_message in messages or []:
        if not isinstance(raw_message, dict):
            continue
        if _is_hidden_context_message(raw_message):
            continue
        role = raw_message.get("role") or ""
        if role == "user":
            if is_same_turn_guidance_message(raw_message):
                flush_group()
                items.append({"type": "guidance", "message": raw_message})
                continue
            flush_group()
            items.append({"type": "user", "message": raw_message})
            continue
        if role == "assistant":
            if assistant_group is None:
                assistant_group = _new_assistant_group()
            assistant_group["messages"].append(raw_message)
            reasoning = raw_message.get("reasoning_content") or raw_message.get("reasoning")
            if reasoning:
                assistant_group["reasoning_segments"].append(reasoning)
            content = raw_message.get("content") or ""
            if content:
                assistant_group["content_segments"].append(content)
            content_parts = raw_message.get("content_parts")
            if isinstance(content_parts, list):
                assistant_group["content_parts"].extend(content_parts)
            for tool_call in raw_message.get("tool_calls") or []:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
                tool_entry = _ensure_tool_entry(
                    assistant_group,
                    tool_call.get("id"),
                    fallback_name=function.get("name") or "unknown_tool",
                    fallback_args=_normalize_tool_args(function.get("arguments")),
                )
                tool_entry["name"] = function.get("name") or tool_entry["name"]
                if function.get("arguments") is not None:
                    tool_entry["args"] = _normalize_tool_args(function.get("arguments"))
            continue
        if role == "tool":
            if assistant_group is None:
                assistant_group = _new_assistant_group()
            assistant_group["messages"].append(raw_message)
            tool_call_id = raw_message.get("tool_call_id")
            tool_entry = _ensure_tool_entry(assistant_group, tool_call_id)
            tool_entry["result"] = raw_message.get("content") or ""
            if raw_message.get("result_obj") is not None:
                tool_entry["result_obj"] = _safe_jsonable(raw_message.get("result_obj"))
            meta = raw_message.get("meta")
            if isinstance(meta, dict) and meta:
                tool_entry["meta"] = meta
            continue
        flush_group()
        items.append({"type": role or "message", "message": raw_message})

    flush_group()
    return items


def build_conversation_render_spans(messages):
    spans = []
    group_start = None
    office_group_start = None

    def flush_group(end_index):
        nonlocal group_start
        if group_start is None:
            return
        spans.append({"start": group_start, "end": end_index})
        group_start = None

    def flush_office_group(end_index):
        nonlocal office_group_start, group_start
        if office_group_start is None:
            return False
        flush_group(end_index)
        spans.append({"start": office_group_start, "end": end_index})
        office_group_start = None
        return True

    for index, raw_message in enumerate(messages or []):
        if not isinstance(raw_message, dict):
            continue
        if _is_hidden_context_message(raw_message):
            continue
        role = raw_message.get("role") or ""
        if role == "user":
            if is_same_turn_guidance_message(raw_message):
                if office_group_start is not None:
                    continue
                if group_start is None:
                    group_start = index
                continue
            flush_office_group(index)
            flush_group(index)
            if _is_office_draft_request(raw_message):
                office_group_start = index
                continue
            spans.append({"start": index, "end": index + 1})
            continue
        if office_group_start is not None:
            continue
        if group_start is None:
            group_start = index

    if office_group_start is not None:
        flush_office_group(len(messages or []))
    flush_group(len(messages or []))
    return spans
