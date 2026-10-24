"""Conversation experts share the ordinary composer, runtime and file preview."""

import copy
import logging
import os
from urllib.parse import unquote

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QPushButton

from core.conversation_agents import agent_role_context, normalize_agent_selection
from core.deliverable_preview import iter_local_file_references, iter_workspace_file_paths, normalize_workspace_file
from core.theme import bind_theme
from ui.conversation_actions import ConversationMentionPopover
from ui.primitives import product_button_style


logger = logging.getLogger(__name__)


class ConversationAgentsMixin:
    def select_conversation_agent(self, profile=None, session_id=None):
        state = self.get_session(session_id) if session_id else self.get_current_session()
        if not state:
            return False
        if self._session_is_busy(state):
            self._show_conversation_notice(state, "当前任务结束或停止后可切换专家；输入已保留。", "info")
            return False
        if not getattr(state, "history_loaded", False) or getattr(state, "history_loading", False):
            self._show_conversation_notice(state, "历史会话加载完成后可切换专家。", "info")
            return False
        selection = normalize_agent_selection(profile or {})
        state.conversation_agent = selection
        self.refresh_conversation_agent_controls(state.session_id)
        self.save_chat_history(session_id=state.session_id)
        logger.info("conversation_agent_selected session=%s agent=%s", state.session_id, (selection or {}).get("id", ""))
        return True

    def refresh_conversation_agent_controls(self, session_id=None):
        if not hasattr(self, "agent_picker_btn") or (session_id and session_id != self.current_session_id):
            return
        state = self.get_current_session()
        selection = normalize_agent_selection(getattr(state, "conversation_agent", None))
        name = (selection or {}).get("name", "专家")
        unavailable = False
        if selection:
            profile = next((p for p in self._available_agent_profiles() if p["id"] == selection["id"]), None)
            unavailable = profile is None
            name = str(profile["name"]) if profile else name + "（不可用）"
        button = self.agent_picker_btn
        button.conversation_agent_name = name
        button.setAccessibleName("当前专家：" + name if selection else "选择专家")
        button.setRemovable(bool(selection))
        button.setVisible(bool(selection))
        ready = bool(state and getattr(state, "history_loaded", False) and not getattr(state, "history_loading", False))
        busy = bool(state and self._session_is_busy(state))
        button.setEnabled(ready and not busy)
        button.setToolTip("任务结束或停止后可切换专家" if busy else (
            "专家已停用或不存在，请重新选择或关闭专家" if unavailable else
            f"{name} · 本会话后续消息继续使用，点击可切换" if selection else "选择本会话使用的专家"
        ))
        self._apply_composer_toolbar_layout(compact=self.input_card.width() < 820)

    def _fit_conversation_agent_chip(self):
        # Keep the role and its close button readable when the editor narrows chat.
        button = self.agent_picker_btn
        button.setMinimumWidth(0)
        button.setMaximumWidth(16777215)
        button.main_btn.setMinimumWidth(0)
        button.main_btn.setMaximumWidth(16777215)
        # Reserve the model, submit/stop buttons and toolbar spacing first.
        margins = self.input_card.layout().contentsMargins()
        available = self.input_card.contentsRect().width() - margins.left() - margins.right()
        for name in ("tool_menu_btn", "permission_button", "action_btn", "stop_btn"):
            control = getattr(self, name, None)
            if control is not None and not control.isHidden():
                available -= (control.minimumWidth() if control.minimumWidth() == control.maximumWidth()
                              else max(control.minimumWidth(), control.sizeHint().width()))
        available -= 100 + 32
        name = getattr(button, "conversation_agent_name", "专家")
        button.setText(name)
        chrome = button.main_btn.sizeHint().width() - button.main_btn.fontMetrics().horizontalAdvance(name)
        chrome += 26 if not button.close_btn.isHidden() else 0
        width = max(12, min(150, available - chrome)) if self.input_card.isVisible() else 150
        button.setText(button.main_btn.fontMetrics().elidedText(name, Qt.ElideRight, width))
        close_width = button.close_btn.width() + button.layout().spacing() if not button.close_btn.isHidden() else 0
        main_width = button.main_btn.sizeHint().width()
        if self.input_card.isVisible():
            main_width = min(main_width, max(1, available - close_width))
        button.main_btn.setFixedWidth(main_width)
        button.setFixedWidth(main_width + close_width)

    def show_conversation_agent_picker(self, *, anchor=None, parent_menu=None):
        state = self.get_current_session()
        if not state or self._session_is_busy(state):
            return
        popover = ConversationMentionPopover(
            [], self._available_agent_profiles(), "", self, agents_only=True,
            selected_agent_id=(getattr(state, "conversation_agent", None) or {}).get("id"))
        self.conversation_agent_popover = popover
        popover.selected.connect(lambda _kind, profile: self.select_conversation_agent(profile, state.session_id))
        footer = QHBoxLayout()
        for title, callback in (("PPT 助手…", self.open_ppt_agent_mode), ("管理专家…", lambda: self.open_settings("专家"))):
            button = QPushButton(title)
            button.setCursor(Qt.PointingHandCursor)
            bind_theme(button, lambda _=None, target=button: target.setStyleSheet(product_button_style("secondary")), surface="composer")
            button.clicked.connect(lambda _checked=False, action=callback: (popover.close_chain(), action()))
            footer.addWidget(button)
        popover.layout().insertLayout(popover.layout().count() - 1, footer)
        anchor = anchor or (self.agent_picker_btn if self.agent_picker_btn.isVisible() else self.tool_menu_btn)
        popover.show_for(anchor, prefer_above=True, parent_popover=parent_menu)
        popover.search.setFocus()

    def _resolve_conversation_agent(self, state, source_message=None, *, dedicated_workflow=False):
        meta = (source_message or {}).get("meta") or {}
        if source_message and "conversation_agent" in meta:
            selection = normalize_agent_selection(meta["conversation_agent"])
            profile = copy.deepcopy(meta.get("conversation_agent_profile"))
        else:
            selection = normalize_agent_selection(getattr(state, "conversation_agent", None))
            profile = None
        if dedicated_workflow:
            return ({} if selection is not None else None), None
        if selection and profile is None:
            profile = next((p for p in self._available_agent_profiles() if p["id"] == selection["id"]), None)
        agent_role_context(selection, profile)  # Validate before committing or clearing the draft.
        if profile:
            selection = {"id": profile["id"], "name": profile["name"]}
        return selection, copy.deepcopy(profile)

    def _conversation_agent_run_context(self, state, workspace):
        selection = getattr(state, "active_turn_agent", None)
        profile = getattr(state, "active_turn_agent_profile", None)
        return agent_role_context(selection, profile, workspace=workspace,
                                  message_id=getattr(state, "active_turn_user_message_id", ""))

    def _complete_conversation_agent(self, state, content, run_id, profile):
        """Called only for a successful, persisted terminal reply, never replay."""
        extensions = (profile or {}).get("auto_preview_extensions") or []
        if not extensions:
            return
        key = (str((profile or {}).get("id") or ""), str(run_id or ""))
        handled = getattr(state, "agent_preview_runs", set())
        if not key[1] or key in handled:
            return
        handled.add(key)
        state.agent_preview_runs = handled
        workspace = self._workspace_dir_for_state(state)
        paths = [path for _start, _end, path in iter_workspace_file_paths(content, workspace)
                 if os.path.splitext(path)[1].lower() in extensions]
        references = [reference for _start, _end, reference in iter_local_file_references(content)
                      if os.path.splitext(unquote(reference).strip('<>"\''))[1].lower() in extensions
                      and not reference.lower().startswith(("http:", "https:"))]
        primary = normalize_workspace_file(references[-1], workspace) if references else ""
        logger.info("conversation_agent_completed session=%s run=%s agent=%s files=%d",
                    state.session_id, run_id, key[0], len(paths))
        if not primary:
            # Discussion need not create a file; only a promised file link needs a diagnostic.
            if references:
                self._show_conversation_notice(state, "回复中的页面文件不存在或不在本会话工作目录，无法自动预览。可检查文件链接或让专家重新生成。", "warning")
            return
        self.register_deliverable_paths(paths, session_id=state.session_id, source="generated", workspace_dir=workspace)
        path = primary
        blocked = bool(getattr(self, "deliverable_edit_dirty", False)) or getattr(self, "deliverable_edit_state", "idle") in {
            "loading", "saving", "restoring", "conflict", "failed",
        }
        current = self.current_session_id == state.session_id and str(getattr(state, "active_turn_request_id", run_id) or "") == str(run_id)
        if current and not blocked:
            try:
                self.open_deliverable_from_chat(path, state.session_id)
                logger.info("conversation_agent_preview_opened session=%s run=%s", state.session_id, run_id)
                return
            except Exception:
                logger.exception("conversation_agent_preview_failed session=%s run=%s", state.session_id, run_id)
                message = "页面已保存，自动预览未能打开，可点击重试。"
        else:
            message = "页面已完成并保存。" + ("请先保存或取消当前编辑，再打开页面。" if current and blocked else "")
        self._show_conversation_notice(state, message, "success", action_text="打开页面",
                                       action_callback=lambda: self._open_agent_deliverable(state.session_id, path))

    def _open_agent_deliverable(self, session_id, path):
        if session_id != self.current_session_id:
            self.activate_session(session_id)
        if self.current_session_id == session_id:
            self.open_deliverable_from_chat(path, session_id)

    def _flush_agent_preview_after_save(self, state, revision):
        pending = getattr(state, "pending_agent_preview", None)
        if pending and revision >= pending["revision"]:
            state.pending_agent_preview = None
            self._complete_conversation_agent(state, pending["content"], pending["run_id"], pending["profile"])
