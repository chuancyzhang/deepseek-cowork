import json
import copy
import logging


def project_visible_messages(messages, *, start=0, end=None):
    """Join provider rounds onto display anchors, without changing the ledger.

    Range arguments address ORIGINAL ledger rows, not the shorter projection.
    This keeps lazy history paging stable when late tool results move to anchors.
    Identity lookup is bounded by ordinary user messages; guidance is a display
    boundary inside that turn, not a new provider conversation.
    """
    messages = list(messages or [])
    slots = [[message] for message in messages]
    fragments = {}
    scopes = []
    scope = -1
    for index, message in enumerate(messages):
        if isinstance(message, dict):
            if message.get("role") == "user" and not is_same_turn_guidance_message(message):
                scope = index
            meta = message.get("meta") or {}
            source = str(meta.get("ui_source_message_id") or "")
            if source and meta.get("ui_visible_fragment"):
                fragments.setdefault((scope, source), []).append(index)
        scopes.append(scope)

    tool_anchors = {}
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        if (message.get("meta") or {}).get("ui_visible_fragment"):
            continue
        source = str(message.get("id") or "")
        anchors = fragments.get((scopes[index], source), [])
        if not anchors:
            continue
        visible = "".join(str(messages[i].get("content") or "") for i in anchors)
        content = str(message.get("content") or "")
        if not content.startswith(visible):
            logging.getLogger(__name__).warning(
                "history_projection_source_conflict source_id=%s ledger_index=%s anchors=%s",
                source, index, anchors,
            )
            continue  # Preserve both records when the identity/content contract fails.
        for anchor in anchors:
            slots[anchor][0] = copy.deepcopy(slots[anchor][0])
        first = slots[anchors[0]][0]
        last = slots[anchors[-1]][0]
        last["content"] = str(last.get("content") or "") + content[len(visible):]
        # Each fragment retains its own text and original stage/group identity.
        for anchor in anchors:
            fragment = slots[anchor][0]
            fragment["content_parts"] = [
                {"type": "text", "text": str(fragment.get("content") or "")}
            ] + [part for part in fragment.get("content_parts") or []
                 if isinstance(part, dict) and part.get("type") != "text"]
        for part in message.get("content_parts") or []:
            if isinstance(part, dict) and part.get("type") != "text" and part not in last["content_parts"]:
                last["content_parts"].append(copy.deepcopy(part))
        if message.get("reasoning_content") or message.get("reasoning"):
            first["reasoning_content"] = message.get("reasoning_content") or message.get("reasoning")
        calls = message.get("tool_calls") or []
        if calls:
            last["tool_calls"] = copy.deepcopy(calls)
            last.setdefault("meta", {})["ui_reply_kind"] = "stage"
        for call in calls:
            if isinstance(call, dict) and call.get("id"):
                tool_anchors[(scopes[index], str(call["id"]))] = anchors[-1]
        slots[index] = []

    for index, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        anchor = tool_anchors.get((scopes[index], str(message.get("tool_call_id") or "")))
        if anchor is not None:
            slots[anchor].append(message)
            slots[index] = []

    # Records written before empty stages became anchors may contain tool-only
    # rounds. Place those by their explicit group and canonical round order,
    # never by text equality or by counting UI stages.
    group_anchors = {}
    for anchors in fragments.values():
        for anchor in anchors:
            meta = messages[anchor].get("meta") or {}
            group = str(meta.get("ui_turn_group_id") or "")
            if group:
                group_anchors.setdefault((scopes[anchor], group), []).append(anchor)
    results = {}
    for index, message in enumerate(messages):
        if isinstance(message, dict) and message.get("role") == "tool":
            results.setdefault((scopes[index], str(message.get("tool_call_id") or "")), []).append(index)
    before = {}
    after = {}
    next_linked = {}
    following_anchors = {}
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        meta = message.get("meta") or {}
        group_key = (scopes[index], str(meta.get("ui_turn_group_id") or ""))
        next_linked[index] = following_anchors.get(group_key)
        linked = fragments.get((scopes[index], str(message.get("id") or "")), [])
        for anchor in reversed(linked):
            group = str((messages[anchor].get("meta") or {}).get("ui_turn_group_id") or "")
            if group:
                following_anchors[(scopes[index], group)] = anchor
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role") != "assistant" or not slots[index]:
            continue
        meta = message.get("meta") or {}
        if meta.get("ui_visible_fragment") or (scopes[index], str(message.get("id") or "")) in fragments:
            continue
        group = str(meta.get("ui_turn_group_id") or "")
        anchors = group_anchors.get((scopes[index], group), [])
        if not anchors:
            continue
        next_anchor = next_linked.get(index)
        target = next_anchor if next_anchor is not None else max(anchors)
        bundle = [message]
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            for result_index in results.get((scopes[index], str(call.get("id") or "")), []):
                bundle.extend(slots[result_index])
                slots[result_index] = []
        (before if next_anchor is not None else after).setdefault(target, []).extend(bundle)
        slots[index] = []
    for anchor in set(before) | set(after):
        slots[anchor] = before.get(anchor, []) + slots[anchor] + after.get(anchor, [])
    return [message for slot in slots[start:end] for message in slot]


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
        and meta.get("kind") in {
            "runtime_context",
            "runtime_context_update",
            "runtime_instruction",
            "skill_context",
            "skill_context_update",
            "skill_state_update",
        }
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


def is_ppt_agent_internal_stage_message(message):
    if not isinstance(message, dict):
        return False
    meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
    return bool(str(meta.get("ppt_agent_internal_stage") or "").strip()) and bool(
        str(meta.get("ppt_task_id") or "").strip()
    )


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
    messages = project_visible_messages(messages)
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
        internal_ppt_stage = is_ppt_agent_internal_stage_message(raw_message)
        if role == "user":
            if internal_ppt_stage:
                continue
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
            content = "" if internal_ppt_stage else raw_message.get("content") or ""
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
            if is_ppt_agent_internal_stage_message(raw_message):
                continue
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
