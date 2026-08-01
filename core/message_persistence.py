AUTO_QUERY_SKILL_CONTEXT_SOURCES = {
    "skill_prompt",
    "skill_prompt_query_match",
    "skill_prompt_tool_search",
    "selected_skill_prompt",
}


def is_auto_query_skill_context_message(message):
    """Return whether a runtime-only Skill context must stay out of chat history."""
    if not isinstance(message, dict):
        return False
    meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
    return bool(
        meta.get("kind") in ("skill_context", "skill_context_update")
        and meta.get("source") in AUTO_QUERY_SKILL_CONTEXT_SOURCES
    )


def filter_persistable_messages(messages):
    """Apply the canonical runtime-only message filter at persistence boundaries."""
    if not isinstance(messages, list):
        return []
    return [
        message
        for message in messages
        if not is_auto_query_skill_context_message(message)
    ]
