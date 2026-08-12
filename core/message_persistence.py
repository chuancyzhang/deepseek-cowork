SKILL_CONTEXT_KINDS = {
    "skill_context",
    "skill_context_update",
    "skill_state_update",
}
LEGACY_AUTO_SKILL_CONTEXT_SOURCES = {
    "skill_prompt",
    "skill_prompt_query_match",
    "skill_prompt_tool_search",
    "selected_skill_prompt",
}


def is_auto_query_skill_context_message(message):
    """Legacy classifier retained for callers that identify old Skill messages."""
    if not isinstance(message, dict):
        return False
    meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
    return bool(
        meta.get("kind") in SKILL_CONTEXT_KINDS
        and (
            bool(str(meta.get("skill_name") or "").strip())
            or meta.get("source") in LEGACY_AUTO_SKILL_CONTEXT_SOURCES
        )
    )


def _filter_ledger_messages(messages, *, include_runtime_repairs=False):
    """Filter UI artifacts while optionally retaining current-run repair context.

    UI projections and incomplete runtime checkpoints belong in the sidecar
    journal.  Persisting them in the provider ledger can create broken tool
    rounds or make a later request treat a local error as model output.
    """
    if not isinstance(messages, list):
        return []
    candidates = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
        if (
            meta.get("ui_only")
            or meta.get("recovery_checkpoint")
            or meta.get("recovered_interrupted")
            or (
                meta.get("runtime_repair_only")
                and not include_runtime_repairs
            )
        ):
            continue
        if (
            str(meta.get("ui_reply_kind") or "").strip().lower()
            in {"error", "interrupted"}
            and not meta.get("context_visible_interruption")
        ):
            continue
        if str(meta.get("source") or "").strip() == "responses_generation_stopped":
            continue
        candidates.append(message)
    return candidates


def filter_persistable_messages(messages):
    """Return only messages that may be committed to the SQLite ledger."""
    return _filter_ledger_messages(messages, include_runtime_repairs=False)


def project_provider_messages(messages, *, include_runtime_repairs=False):
    candidates = _filter_ledger_messages(
        messages,
        include_runtime_repairs=include_runtime_repairs,
    )
    projected, excluded_ids = project_canonical_messages(candidates)
    candidate_ids = {
        str(message.get("id") or "")
        for message in candidates
        if isinstance(message, dict) and str(message.get("id") or "")
    }
    explicitly_excluded_ids = [
        str(message.get("id") or "")
        for message in (messages or [])
        if isinstance(message, dict)
        and str(message.get("id") or "")
        and str(message.get("id") or "") not in candidate_ids
    ]
    return projected, list(dict.fromkeys(explicitly_excluded_ids + excluded_ids))


def project_canonical_messages(messages):
    """Project a replay-safe ledger without mutating the stored history.

    Legacy SQLite rows remain untouched. Broken or orphaned tool events are
    excluded only from the provider projection and are returned for sidecar
    diagnostics.
    """
    source = [message for message in (messages or []) if isinstance(message, dict)]
    projected = []
    excluded_ids = []
    seen_call_ids = set()
    index = 0

    def exclude(message):
        message_id = str(message.get("id") or "").strip()
        if message_id and message_id not in excluded_ids:
            excluded_ids.append(message_id)

    while index < len(source):
        message = source[index]
        role = str(message.get("role") or "").strip().lower()
        if role == "tool":
            exclude(message)
            index += 1
            continue
        raw_calls = message.get("tool_calls") if role == "assistant" else None
        if not raw_calls:
            projected.append(message)
            index += 1
            continue
        if not isinstance(raw_calls, list) or not raw_calls:
            exclude(message)
            index += 1
            continue
        call_ids = []
        valid_calls = True
        for call in raw_calls:
            function = call.get("function") if isinstance(call, dict) else None
            call_id = str(call.get("id") or "").strip() if isinstance(call, dict) else ""
            name = str(function.get("name") or "").strip() if isinstance(function, dict) else ""
            if not call_id or not name or call_id in seen_call_ids or call_id in call_ids:
                valid_calls = False
                break
            call_ids.append(call_id)
        following_tools = []
        cursor = index + 1
        while cursor < len(source) and str(source[cursor].get("role") or "").lower() == "tool":
            following_tools.append(source[cursor])
            cursor += 1
        delivered_ids = [
            str(tool_message.get("tool_call_id") or "").strip()
            for tool_message in following_tools
        ]
        delivered_id_set = set(delivered_ids)
        if (
            valid_calls
            and len(delivered_ids) == len(call_ids)
            and "" not in delivered_id_set
            and len(delivered_id_set) == len(delivered_ids)
            and delivered_id_set == set(call_ids)
        ):
            projected.append(message)
            projected.extend(following_tools)
            seen_call_ids.update(call_ids)
        else:
            exclude(message)
            for tool_message in following_tools:
                exclude(tool_message)
        index = cursor
    return projected, excluded_ids


def fold_skill_state_events(messages):
    """Fold append-only Skill events without mutating or removing their history."""
    states = {}
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
        kind = str(meta.get("kind") or "")
        if kind not in SKILL_CONTEXT_KINDS:
            continue
        skill_name = str(meta.get("skill_name") or "").strip()
        if not skill_name:
            continue
        previous = states.get(skill_name, {})
        state = str(meta.get("state") or ("enabled" if kind == "skill_context" else "")).strip().lower()
        if state not in {"enabled", "disabled"}:
            state = str(previous.get("state") or "enabled")
        content_hash = str(meta.get("content_hash") or previous.get("content_hash") or "")
        try:
            catalog_revision = int(meta.get("catalog_revision") or previous.get("catalog_revision") or 0)
        except (TypeError, ValueError):
            catalog_revision = int(previous.get("catalog_revision") or 0)
        states[skill_name] = {
            "state": state,
            "content_hash": content_hash,
            "catalog_revision": catalog_revision,
            "source": str(meta.get("source") or previous.get("source") or ""),
            "selection_scoped": bool(
                meta.get("selection_scoped")
                if "selection_scoped" in meta
                else previous.get("selection_scoped")
            ),
            "message_id": str(message.get("id") or ""),
        }
    return states
