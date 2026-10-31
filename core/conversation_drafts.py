"""Shared rules for meaningful conversation drafts and their history projection."""

from copy import deepcopy


DEFAULT_CONVERSATION_TITLES = {"", "新任务", "新对话", "新聊天"}


def composer_snapshot(text="", files=()):
    text = str(text or "")
    files = list(files or [])
    return {"text": text, "files": deepcopy(files)} if text.strip() or files else {}


def saved_composer(meta):
    meta = meta or {}
    for key in ("composer_draft", "update_composer"):
        value = meta.get(key)
        if isinstance(value, dict):
            return composer_snapshot(value.get("text"), value.get("files"))
    return composer_snapshot(meta.get("composer_reference_draft"))


def conversation_has_content(meta, *, has_messages=False, title="", status=""):
    """Defaults/workspace allocation alone never turn a blank view into history."""
    if has_messages:
        return True
    meta = meta or {}
    if saved_composer(meta):
        return True
    if any(meta.get(key) for key in (
        "selected_skill_names", "conversation_agent", "knowledge_refs",
        "composer_conversation_refs", "pending_clarify_questions", "manual_title",
        "pinned", "has_file_changes", "ui_timeline_v1", "team_space_required", "workspace_pages",
    )):
        return True
    if meta.get("grill_mode_state") in {"armed", "active"}:
        return True
    if str(status or meta.get("session_status") or "").lower() in {
        "running", "stopping", "waiting", "interrupted", "stopped", "completed", "failed", "error",
    }:
        return True
    if str(meta.get("run_phase") or "idle").lower() not in {"idle", "待开始"}:
        return True
    # Preserve explicitly named older records even if they predate manual_title.
    return str(title or "").strip() not in DEFAULT_CONVERSATION_TITLES


def history_record_has_content(record):
    # Older/custom providers without the existence flag cannot prove emptiness.
    if record.get("has_messages") is not False or record.get("im_provider"):
        return True
    return conversation_has_content(record.get("meta"), title=record.get("title"),
                                    status=record.get("status"))


def history_record_title(record):
    title = str(record.get("title") or "").strip()
    if record.get("has_messages") is not False or title not in DEFAULT_CONVERSATION_TITLES:
        return title or "新任务"
    meta = record.get("meta") or {}
    draft = saved_composer(meta)
    text = " ".join(str(draft.get("text") or "").split())
    if text:
        return "草稿 · " + (text[:24] + "…" if len(text) > 24 else text)
    if draft.get("files"):
        return f"草稿 · {len(draft['files'])} 个附件"
    if meta.get("workspace_pages"):
        page = meta["workspace_pages"][0]
        return "页面 · " + str(page.get("title") or "无标题页")[:24]
    if meta.get("composer_conversation_refs") or meta.get("knowledge_refs"):
        return "草稿 · 已添加引用"
    if meta.get("selected_skill_names"):
        return f"草稿 · 已选 {len(meta['selected_skill_names'])} 个技能"
    if meta.get("conversation_agent"):
        return "草稿 · 已选择专家"
    if meta.get("grill_mode_state") == "armed":
        return "草稿 · 拷问模式"
    return title or "新任务"
