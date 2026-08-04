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


def filter_persistable_messages(messages):
    """Preserve every provider-visible ledger entry in its original order."""
    if not isinstance(messages, list):
        return []
    return [message for message in messages if isinstance(message, dict)]


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
