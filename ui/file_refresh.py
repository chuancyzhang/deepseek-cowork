"""Debounced external-file refresh without replacing an unsaved draft."""
import logging
import os
from PySide6.QtCore import QTimer
from core.deliverable_editing import sha256_file
from core.preview_versions import html_resources, path_key
from ui.worker_lifecycle import ui_work_stopping

log = logging.getLogger(__name__)


def preview_resources(window, path):
    from ui.preview_refresh import controller
    return controller(window).resources(path)


def schedule_file_refresh(window, changed_path=""):
    if ui_work_stopping(window):
        return
    current = getattr(window, "current_deliverable_path", "")
    if not current:
        return
    if changed_path:
        related = {path_key(current)} | set(getattr(window, "_deliverable_preview_resources", ()))
        if path_key(changed_path) not in related | {os.path.dirname(path) for path in related}:
            return
    window._file_refresh_scope = (getattr(window, "current_session_id", ""), path_key(current))
    timer = getattr(window, "browser_file_refresh_timer", None)
    if timer is None:
        timer = QTimer(window)
        timer.setSingleShot(True)
        timer.setInterval(250)
        timer.timeout.connect(lambda: refresh_file(window))
        window.browser_file_refresh_timer = timer
    timer.start()


def refresh_file(window):
    if ui_work_stopping(window):
        return
    browser = getattr(window, "browser_workbench", None)
    path = getattr(window, "current_deliverable_path", "")
    if not path or (browser and (browser.closed or not browser.file_active())):
        return
    scope = getattr(window, "_file_refresh_scope", None)
    if scope is not None and scope != (getattr(window, "current_session_id", ""), path_key(path)):
        return
    if getattr(window, "deliverable_edit_state", "idle") in {"saving", "loading", "restoring"}:
        schedule_file_refresh(window)
        return
    session = getattr(window, "deliverable_edit_session", None)
    try:
        changed = not os.path.isfile(path)
        if session and not changed:
            changed = sha256_file(path) != session.initial_fingerprint
        if session:
            if not changed:
                return
            if window.deliverable_edit_dirty or not os.path.isfile(path):
                window.deliverable_editor_external_conflict = True
                window._set_deliverable_edit_state("conflict", "磁盘文件已变化；你的草稿仍保留。请另存副本或放弃草稿并重新加载。")
                log.info("file.conflict session=%s missing=%s", window.current_session_id, not os.path.isfile(path))
                return
            mode = session.metadata.get("editor_mode", "")
            position = window.deliverable_text_editor.verticalScrollBar().value() if session.descriptor.kind == "text" else 0
            window._release_deliverable_edit_session(show_preview=False)
            window.begin_deliverable_edit(editor_mode=mode)
            window._browser_restore_editor_scroll = position
        else:
            from ui.preview_refresh import controller, managed_preview
            if managed_preview(path):
                controller(window).check(path)
                return
            if not os.path.isfile(path):
                window._set_deliverable_status("文件已移走或删除，可从目录重新选择。", tone="warning")
                return
            if window._deliverable_fingerprint(path) == getattr(window, "deliverable_render_fingerprint", None):
                return
            preview = window.preview_stack.currentWidget()
            position = preview.verticalScrollBar().value() if hasattr(preview, "verticalScrollBar") else None
            window.current_deliverable_stale = True
            window.render_selected_deliverable(force=True)
            if position is not None and hasattr(preview, "verticalScrollBar"):
                QTimer.singleShot(0, lambda: preview.verticalScrollBar().setValue(position))
        log.info("file.refresh session=%s editing=%s", window.current_session_id, bool(session))
    except (OSError, RuntimeError) as exc:
        log.warning("file.refresh_error session=%s error=%s", window.current_session_id, exc)
        window._set_deliverable_status("文件刷新失败，可稍后刷新重试。", tone="warning")
    finally:
        window._watch_deliverable_paths()
