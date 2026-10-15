"""Desktop entry points for references, forks and regeneration."""

import copy
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from shiboken6 import isValid
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout, QWidget,
)

from core.conversation_actions import (
    clone_branch, retain_branch_text_history, regeneration_source, write_reference_snapshot,
)
from core.theme import DesignTokens, bind_theme
from core.inline_visualization import find_inline_visualization_files
from ui.primitives import ProductInlineNotice, ProductPopover, ProductSegmentedControl


logger = logging.getLogger(__name__)


def reference_button(text, parent=None):
    button = QPushButton(text, parent)
    button.setCursor(Qt.PointingHandCursor)
    button.setObjectName("ConversationContextLink")
    bind_theme(button, lambda _=None: button.setStyleSheet(
        f"QPushButton#ConversationContextLink {{ border: none; background: transparent; "
        f"color: {DesignTokens.text_secondary}; font-size: {DesignTokens.font_size_meta}px; "
        f"font-weight: 400; padding: 3px 5px; text-align: left; }} "
        f"QPushButton#ConversationContextLink:hover {{ color: {DesignTokens.primary}; "
        f"background: {DesignTokens.bg_hover}; border-radius: {DesignTokens.radius_sm}px; }}"
    ), surface="conversation")
    return button


class ConversationMentionPopover(ProductPopover):
    selected = Signal(str, object)

    def __init__(self, conversations, profiles, workspace, parent, *, agents_only=False):
        super().__init__(parent, width=420)
        self.conversations = conversations
        self.profiles = profiles
        self.workspace = workspace
        layout = QVBoxLayout(self)
        self.kind = "智能体" if agents_only else "会话"
        self.tabs = ProductSegmentedControl([("会话", "引用会话"), ("智能体", "选择专家")], current=self.kind)
        layout.addWidget(self.tabs)
        self.tabs.setVisible(not agents_only)
        self.hint = QLabel("把其他会话作为参考资料，在当前会话回答")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.tabs.currentChanged.connect(self.change_kind)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索会话标题或项目")
        layout.addWidget(self.search)
        self.items = QListWidget()
        self.items.setMinimumHeight(250)
        layout.addWidget(self.items)
        self.empty = QLabel("没有匹配的会话")
        layout.addWidget(self.empty)
        self.search.textChanged.connect(self.refresh_items)
        self.items.itemClicked.connect(self.choose)
        self.items.itemActivated.connect(self.choose)
        self.search.returnPressed.connect(lambda: self.choose(self.items.currentItem()))
        bind_theme(self, self.refresh_picker_theme, surface="composer")
        self.change_kind(self.kind)

    def refresh_picker_theme(self, _=None):
        self.search.setStyleSheet(f"QLineEdit {{ color: {DesignTokens.text_primary}; background: {DesignTokens.bg_main}; border: 1px solid {DesignTokens.border_subtle}; padding: 6px; }}")
        self.items.setStyleSheet(f"QListWidget {{ color: {DesignTokens.text_primary}; background: {DesignTokens.bg_main}; border: none; }} QListWidget::item {{ padding: 7px; }} QListWidget::item:selected {{ background: {DesignTokens.primary_soft}; color: {DesignTokens.text_primary}; }}")
        self.empty.setStyleSheet(f"color: {DesignTokens.text_secondary};")
        self.hint.setStyleSheet(f"color: {DesignTokens.text_secondary}; font-size: {DesignTokens.font_size_meta}px;")

    def change_kind(self, kind):
        self.kind = kind
        self.search.setPlaceholderText("搜索会话标题或项目" if kind == "会话" else "搜索专家名称或用途")
        self.hint.setText("把其他会话作为参考资料，在当前会话回答" if kind == "会话" else "选择后，本会话后续消息继续使用这位专家")
        self.empty.setText("没有匹配的" + ("会话" if kind == "会话" else "专家"))
        self.refresh_items()

    def refresh_items(self):
        query = self.search.text().strip().casefold()
        self.items.clear()
        records = sorted(self.conversations, key=lambda row: (
            (row.get("meta") or {}).get("workspace_dir") != self.workspace,
            -(row.get("updated_at") or 0),
        ))
        first = None
        for kind, entries in ((self.kind, records if self.kind == "会话" else self.profiles),):
            found = []
            for record in entries:
                meta = record.get("meta") or {}
                title = record.get("title") or record.get("name") or "新对话"
                workspace = meta.get("workspace_dir") or ""
                project = os.path.basename(workspace) if meta.get("workspace_source") == "project" else "独立聊天"
                if query and query not in (title + " " + project + " " + str(record.get("description") or "")).casefold():
                    continue
                subtitle = record.get("description") or ""
                if kind != "会话" and record.get("builtin"):
                    title += " · 内置"
                if kind == "会话":
                    updated = datetime.fromtimestamp(record.get("updated_at") or 0).strftime("%m-%d %H:%M")
                    subtitle = f"{project} · {updated}" + (" · 已归档" if meta.get("archived") or record.get("status") == "archived" else "")
                found.append((record, f"{title}\n{subtitle}"))
            if not found:
                continue
            for record, text in found:
                item = QListWidgetItem(text, self.items)
                item.setData(Qt.UserRole, (kind, record))
                first = first or item
        self.empty.setVisible(first is None)
        if first is not None:
            self.items.setCurrentItem(first)

    def choose(self, item):
        data = item.data(Qt.UserRole) if item is not None else None
        if data:
            self.selected.emit(*data)
            self.close()


class BranchWorker(QThread):
    completed = Signal(str)
    failed = Signal(str)

    def __init__(self, storage, target_id, title, messages, meta, parent):
        super().__init__(parent)
        self.storage, self.target_id, self.title = storage, target_id, title
        self.messages, self.meta = messages, meta

    def run(self):
        try:
            if self.meta["workspace_source"] == "chat":
                retain_branch_text_history(self.messages)
                self.meta["branch_history_only"] = True
            artifacts = self.meta.pop("branch_visualizations", [])
            self.storage.create_branch_conversation(self.target_id, self.messages, title=self.title, meta=self.meta, artifacts=artifacts)
        except Exception as exc:
            logger.exception("conversation_branch_failed target=%s", self.target_id)
            self.failed.emit(str(exc))
            return
        logger.info("conversation_branch_created target=%s messages=%s", self.target_id, len(self.messages))
        self.completed.emit(self.target_id)


class ConversationActionsMixin:
    def show_conversation_mention_menu(self):
        state = self.get_current_session()
        if not state:
            return
        cursor = self.input_field.textCursor()
        position = cursor.position()
        text = self.input_field.toPlainText()
        # An email address is ordinary text, not a mention picker trigger.
        if position < 1 or text[position - 1:position] != "@" or (position > 1 and not text[position - 2].isspace()):
            return
        records = [row for row in self.chat_storage.list_conversation_summaries() if row["id"] != state.session_id]
        popover = ConversationMentionPopover(records, self._available_agent_profiles(), self._workspace_dir_for_state(state), self)
        self.conversation_mention_popover = popover

        def selected(kind, record):
            if self.current_session_id != state.session_id:
                return
            current = self.input_field.toPlainText()
            if current != text:
                return
            if kind == "智能体" and not self.select_conversation_agent(record, state.session_id):
                return
            selection = self.input_field.textCursor()
            selection.setPosition(position - 1)
            selection.setPosition(position, QTextCursor.KeepAnchor)
            selection.removeSelectedText()
            self.input_field.setTextCursor(selection)
            if kind == "智能体":
                self._save_reference_draft(state)
            else:
                refs = getattr(state, "conversation_refs", [])
                if not any(ref["session_id"] == record["id"] for ref in refs):
                    refs.append({"session_id": record["id"], "title": record.get("title") or "新对话"})
                state.conversation_refs = refs
                self.refresh_conversation_reference_chips()
                self._save_reference_draft(state)
            self.input_field.setFocus()

        popover.selected.connect(selected)
        popover.show_for(self.input_field, prefer_above=True)
        popover.search.setFocus()

    def _save_reference_draft(self, state):
        if state.session_id == self.current_session_id:
            state.composer_draft = self.input_field.toPlainText()
        if getattr(state, "history_loaded", True) and not getattr(state, "history_loading", False):
            self.save_chat_history(session_id=state.session_id)

    def queue_reference_draft_save(self):
        state = self.get_current_session()
        if not state or not getattr(state, "conversation_refs", []):
            return
        state.composer_draft = self.input_field.toPlainText()
        timer = getattr(state, "reference_draft_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda s=state: self._save_reference_draft(s))
            state.reference_draft_timer = timer
        timer.start(600)

    def refresh_conversation_reference_chips(self):
        container = getattr(self, "conversation_reference_chips", None)
        if container is None:
            return
        layout = container.layout()
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        state = self.get_current_session()
        refs = getattr(state, "conversation_refs", []) if state else []
        for ref in refs:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            button = reference_button("引用：" + ref["title"])
            button.setText(button.fontMetrics().elidedText("引用：" + ref["title"], Qt.ElideRight, 300))
            button.setToolTip(ref["title"] + "\n作为参考资料，发送时固定上下文。点击查看。")
            button.clicked.connect(lambda checked=False, r=copy.deepcopy(ref): self.show_conversation_reference(r))
            remove = reference_button("×")
            remove.setFixedWidth(24)
            remove.setToolTip("移除此会话引用")
            remove.clicked.connect(lambda checked=False, sid=state.session_id, rid=ref["session_id"]: self.remove_conversation_reference(sid, rid))
            row_layout.addWidget(button)
            row_layout.setSpacing(0)
            row_layout.addWidget(remove)
            row_layout.addStretch()
            layout.addWidget(row)
        container.setVisible(bool(refs))

    def remove_conversation_reference(self, session_id, reference_session_id):
        state = self.get_session(session_id)
        if state:
            state.conversation_refs = [ref for ref in state.conversation_refs if ref["session_id"] != reference_session_id]
            self._save_reference_draft(state)
            if session_id == self.current_session_id:
                self.refresh_conversation_reference_chips()

    def prepare_conversation_references(self, state, references):
        result, created = [], []
        # Existing text tools read within the target workspace. A per-session
        # subdirectory also separates snapshots in a shared project directory.
        workspace = self._ensure_session_workspace(state)
        if not workspace:
            raise ValueError("当前会话工作目录不可用。")
        directory = Path(workspace) / ".cowork" / state.session_id
        try:
            for ref in references:
                if ref.get("history_only"):
                    continue
                if ref.get("snapshot_path"):
                    if not all(os.path.isfile(path) for path in [ref["snapshot_path"], *(ref.get("snapshot_parts") or [])]):
                        raise ValueError(f"“{ref['title']}”的引用快照不存在，请重新选择来源。")
                    result.append(copy.deepcopy(ref))
                else:
                    source_state = self.get_session(ref["session_id"])
                    if source_state:
                        self._flush_ui_stream_buffers(source_state)
                    snapshot = self._capture_chat_log_snapshot(ref["session_id"])
                    prepared = write_reference_snapshot(snapshot, directory)
                    created.append(prepared["snapshot_path"])
                    created.extend(prepared.get("snapshot_parts") or [])
                    result.append(prepared)
        except Exception:
            for path in created:
                Path(path).unlink(missing_ok=True)
            raise
        return result

    def show_conversation_reference(self, ref):
        if ref.get("snapshot_path"):
            self._open_exported_file(ref["snapshot_path"])
        else:
            self.open_conversation_source(ref["session_id"])

    def open_conversation_source(self, session_id):
        if not self.chat_storage.has_conversation(session_id) and not self.get_session(session_id):
            self.add_system_toast("来源会话已删除，已保存的引用快照仍可查看。", "info")
            return
        self.load_session(session_id)

    def attach_conversation_reference_actions(self, bubble, state):
        if bubble.role != "User":
            return
        message = self._find_message_by_id(state.messages, bubble.source_message_id) or {}
        meta = message.get("meta") or {}
        names = meta.get("branch_historical_files") or []
        if names:
            label = QLabel("历史附件未带入：" + "、".join(names[:3]) + (f" 等 {len(names)} 个文件" if len(names) > 3 else ""))
            label.setTextFormat(Qt.PlainText)
            label.setWordWrap(True)
            label.setToolTip("需要时请重新添加。\n" + "\n".join(names))
            bind_theme(label, lambda _=None, widget=label: widget.setStyleSheet(
                f"color: {DesignTokens.text_secondary}; font-size: {DesignTokens.font_size_meta}px;"), surface="conversation")
            (bubble.user_wrapper_layout or bubble.layout()).addWidget(label)
        refs = meta.get("conversation_refs") or []
        if not refs:
            return
        row = QWidget(bubble)
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        for ref in refs:
            buttons = QHBoxLayout()
            if ref.get("history_only"):
                source = reference_button("历史引用：" + ref["title"] + " · 打开来源")
                source.setToolTip("原对话的引用记录；快照文件未带入此分支。如需使用，请重新 @ 引用。")
                source.clicked.connect(lambda checked=False, sid=ref["session_id"]: self.open_conversation_source(sid))
                buttons.addWidget(source)
                buttons.addStretch()
                layout.addLayout(buttons)
                continue
            snapshot = reference_button("@" + ref["title"] + " · 查看引用快照")
            snapshot.setToolTip("引用时间：" + ref.get("captured_at", ""))
            snapshot.clicked.connect(lambda checked=False, r=copy.deepcopy(ref): self.show_conversation_reference(r))
            source = reference_button("打开来源")
            source.clicked.connect(lambda checked=False, sid=ref["session_id"]: self.open_conversation_source(sid))
            buttons.addWidget(snapshot)
            buttons.addWidget(source)
            buttons.addStretch()
            layout.addLayout(buttons)
        (bubble.user_wrapper_layout or bubble.layout()).addWidget(row)

    def regenerate_conversation_answer(self, session_id, message_id):
        state = self.get_session(session_id)
        if not state:
            return False
        try:
            source = regeneration_source(state.messages, message_id)
        except ValueError as exc:
            self._show_conversation_notice(state, str(exc), "warning")
            return False
        return self.edit_user_message_inline(session_id, source["id"], self._message_display_content(source), regenerate=True)

    def branch_conversation(self, session_id, message_id):
        state = self.get_session(session_id)
        if not state or getattr(state, "branch_in_progress", False):
            return
        if not getattr(state, "history_loaded", False) or getattr(state, "history_loading", False):
            self._show_conversation_notice(state, "历史消息仍在加载，请稍后再分支。", "info")
            return
        self._flush_ui_stream_buffers(state)
        target_id = uuid.uuid4().hex
        try:
            title = self._resolved_session_title(state)
            messages, meta = clone_branch(state.messages, self._compose_session_meta(state), target_id, session_id, title, message_id)
            workspace = self._workspace_dir_for_state(state)
            project = self._session_workspace_source(state) == "project"
            destination = workspace if project else self._chat_workspace_dir_for_session(target_id)
            meta.update(workspace_dir=destination, workspace_source="project" if project else "chat")
            # Project branches reuse existing assets; chat branches carry records only.
            if project:
                artifacts = []
                for file in {file for message in messages for file in find_inline_visualization_files(message.get("content") or "")}:
                    artifact = self.chat_storage.get_inline_visualization(session_id, file)
                    if artifact:
                        artifacts.append(artifact)
                meta["branch_visualizations"] = artifacts
        except Exception as exc:
            logger.exception("conversation_branch_prepare_failed source=%s", session_id)
            self._show_conversation_notice(state, f"无法创建分支：{exc}", "error")
            return
        worker = BranchWorker(self.chat_storage, target_id, title + " · 分支", messages, meta, self)
        state.branch_in_progress = True
        state.branch_worker = worker
        # Keep operation progress separate from the session's shared error notice.
        progress = ProductInlineNotice("正在创建分支…", "info")
        state.chat_layout.insertWidget(max(0, state.chat_layout.count() - 1), progress)

        def clear_progress():
            if isValid(progress):
                state.chat_layout.removeWidget(progress)
                progress.hide()
                progress.deleteLater()

        def completed(new_id):
            clear_progress()
            self.sync_current_session_state()
            self.refresh_history_list()
            self.load_session(new_id)
            self.input_field.setFocus()

        worker.completed.connect(completed)
        def failed(error):
            clear_progress()
            self._show_conversation_notice(state, f"分支创建失败，原会话已保留：{error}", "error")

        worker.failed.connect(failed)
        def finished():
            clear_progress()
            state.branch_in_progress = False
            state.branch_worker = None
        worker.finished.connect(finished)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def refresh_branch_origin(self, state):
        button = getattr(self, "branch_origin_button", None)
        if button is None or not state or state.session_id != self.current_session_id:
            return
        meta = getattr(state, "persisted_conversation_meta", {}) or {}
        origin = meta.get("branch_origin") or {}
        self.branch_origin_row.setVisible(bool(origin))
        if origin:
            shared = meta.get("workspace_source") == "project"
            button.setText("查看原会话" + (" · 共用项目文件" if shared else ""))
            button.setToolTip("分支来源：" + origin["title"])
            button.setProperty("sourceSessionId", origin["session_id"])
            if not shared and meta.get("branch_history_only"):
                button.setToolTip("分支来源：" + origin["title"] + "\n仅继承对话记录，历史文件未带入；需要时请重新添加。")
