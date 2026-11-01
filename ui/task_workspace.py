"""Conversation-owned presentation state; never part of the model workbench registry."""
from dataclasses import dataclass, field
import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication

log = logging.getLogger(__name__)


TASK_TAB_KEY = "@task"


@dataclass
class TaskWorkspaceView:
    opened: bool = True
    active: bool = False
    section: int = 1  # Calls; matches MainWindow.OBS_SECTION_LOG.
    detail: dict | None = None
    subagent_id: str = ""
    list_scroll: int = 0
    selected_call: str = ""
    viewer_positions: list = field(default_factory=list)
    args_expanded: bool = True
    info_expanded: bool = False
    context_expanded: bool = False


def task_workspace_view(session):
    if session is None:
        return None
    view = getattr(session, "task_workspace_view", None)
    if not isinstance(view, TaskWorkspaceView):
        view = TaskWorkspaceView()
        session.task_workspace_view = view
    return view


class TaskWorkspaceMixin:
    """Route task views through the workspace without changing runtime tabs."""

    def capture_task_workspace_view(self):
        state = self.get_session(getattr(self, "_task_view_session_id", ""))
        if state is None or getattr(self, "_restoring_task_view", False):
            return
        view = task_workspace_view(state)
        view.section = self.observability_section_index
        view.list_scroll = self.step_list.verticalScrollBar().value()
        selected = self.step_list.currentItem()
        view.selected_call = selected.data(Qt.UserRole) if selected else ""
        monitor = self.sub_agent_monitor
        if monitor.conversation_id == state.session_id:
            view.subagent_id = monitor.agent_id
            monitor._save_view()
        view.args_expanded = self.td_args_toggle.isChecked()
        view.info_expanded = self.td_info_toggle.isChecked()
        view.context_expanded = self.context_raw_toggle.isChecked()
        view.viewer_positions = [
            (edit.verticalScrollBar().value(), edit.textCursor().anchor(), edit.textCursor().position())
            for edit in (self.td_args_edit, self.td_args_meta_edit, self.td_result_edit)
        ]
        if getattr(self, "_tool_detail_session_id", None) == state.session_id and not self.td_detail_splitter.isHidden():
            view.detail = dict(
                tool_id=getattr(self, "current_selected_tool_id", None),
                title=self.td_info_label.text(), status=self.td_status_label.text(),
                meta=self.td_meta_label.text(), args=self.td_args_edit.toPlainText(),
                language=self.td_args_edit._language,
                other=self.td_args_meta_edit.toPlainText() if not self.td_args_meta_edit.isHidden() else "",
                result=getattr(self, "_detail_full_result", ""),
                full_args=getattr(self, "_detail_full_arguments", ""),
            )

    def restore_task_workspace_view(self, state, active=False):
        view = task_workspace_view(state)
        self._restoring_task_view = True
        try:
            self._task_view_session_id = state.session_id
            self._tool_detail_session_id = state.session_id
            detail = view.detail or {}
            self.current_selected_tool_id = detail.get("tool_id")
            self._detail_full_result = detail.get("result", "")
            self._detail_full_arguments = detail.get("full_args", "")
            self.td_info_label.setText(detail.get("title", "选择一条调用"))
            self.td_status_label.setText(detail.get("status", ""))
            self.td_meta_label.setText(detail.get("meta", ""))
            self.td_args_edit.set_code(detail.get("args", ""), detail.get("language", "json"))
            self.td_args_meta_edit.set_code(detail.get("other", ""), "json")
            self.td_args_meta_label.setVisible(bool(detail.get("other")))
            self.td_args_meta_edit.setVisible(bool(detail.get("other")))
            self.td_result_edit.set_result(self._detail_full_result)
            self.td_detail_splitter.setVisible(bool(detail))
            self.td_empty_action.setVisible(not bool(detail))
            self.td_args_toggle.setChecked(view.args_expanded)
            self._set_tool_arguments_expanded(view.args_expanded)
            self.td_info_toggle.setChecked(view.info_expanded)
            self.td_info_toggle.setVisible(bool(detail))
            self.td_meta_label.setVisible(bool(detail) and view.info_expanded)
            self.context_raw_toggle.setChecked(view.context_expanded)
            self._refresh_subagent_panel_context()
            if view.subagent_id and self.sub_agent_monitor.agent_id != view.subagent_id:
                self.sub_agent_monitor.open_agent(view.subagent_id)
            elif not view.subagent_id and self.sub_agent_monitor.agent_id:
                self.sub_agent_monitor.show_overview()
            self.set_observability_section(view.section)
            for index in range(self.step_list.count()):
                item = self.step_list.item(index)
                if item.data(Qt.UserRole) == view.selected_call:
                    self.step_list.setCurrentItem(item)
                    break
            self.step_list.verticalScrollBar().setValue(view.list_scroll)
            for edit, position in zip((self.td_args_edit, self.td_args_meta_edit, self.td_result_edit), view.viewer_positions):
                scroll, anchor, end = position
                limit = max(0, edit.document().characterCount() - 1)
                cursor = edit.textCursor()
                cursor.setPosition(min(anchor, limit))
                cursor.setPosition(min(end, limit), QTextCursor.KeepAnchor)
                edit.setTextCursor(cursor)
                edit.verticalScrollBar().setValue(scroll)
            view.active = bool(active and view.opened)
            if view.active:
                self.right_drawer_tab = self.RIGHT_TAB_OBSERVABILITY
                self.right_stack.setCurrentWidget(self.tool_details_tab)
            self.update_context_drawer_header(self.right_drawer_tab)
        finally:
            self._restoring_task_view = False

    def open_task_workspace(self, section=None):
        state = self.get_current_session()
        if state is None:
            return False
        view = task_workspace_view(state)
        if not view.active and not self.confirm_leave_deliverable_edit("切换标签"):
            return False
        self.browser_workbench.capture_file_position()
        self.browser_workbench.pause_follow()
        view.opened = True
        view.active = True
        self._workspace_chat_selected = False
        self._task_view_session_id = state.session_id
        self.right_drawer_tab = self.RIGHT_TAB_OBSERVABILITY
        self.right_drawer_open = True
        self.right_stack.setCurrentWidget(self.tool_details_tab)
        if section is not None:
            self.set_observability_section(section)
        else:
            self._sync_task_navigation()
        if self.observability_section_index == self.OBS_SECTION_AGENTS:
            self._refresh_subagent_panel_context()
        elif self.observability_section_index == self.OBS_SECTION_DETAILS:
            tool_id = getattr(self, "current_selected_tool_id", None)
            card = state.tool_cards.get(tool_id)
            if card is not None:
                self.show_tool_details(tool_id, card.args, card.result, meta=card.meta, switch_tab=False)
        self.update_context_drawer_header(self.RIGHT_TAB_OBSERVABILITY)
        self.right_sidebar.show()
        self.sync_context_drawer_layout()
        self.update_context_rail_badges()
        log.info("task_workspace.opened session=%s section=%s", state.session_id, self.observability_section_index)
        return True

    def _sync_task_navigation(self):
        index = getattr(self, "observability_section_index", 1)
        monitor = getattr(self, "sub_agent_monitor", None)
        drilldown = index == self.OBS_SECTION_DETAILS or (
            index == self.OBS_SECTION_AGENTS and monitor is not None and bool(monitor.agent_id)
        )
        self.observability_segment_control.setVisible(not drilldown)
        key = {self.OBS_SECTION_LOG: "log", self.OBS_SECTION_PROMPT: "prompt", self.OBS_SECTION_AGENTS: "agents"}.get(index)
        for name, button in self.observability_segment_control.buttons.items():
            button.setChecked(name == key)

    def _subagent_workspace_visible(self):
        return (getattr(self, "right_drawer_open", False)
                and getattr(self, "right_drawer_tab", None) == self.RIGHT_TAB_OBSERVABILITY
                and self.observability_section_index == self.OBS_SECTION_AGENTS
                and not getattr(self, "_workspace_chat_selected", False))

    def _set_tool_arguments_expanded(self, expanded):
        self.td_args_body.setVisible(bool(expanded))
        self.td_args_section.setMaximumHeight(16777215 if expanded else 36)

    def copy_tool_arguments(self):
        QApplication.clipboard().setText(getattr(self, "_detail_full_arguments", ""))
        self.td_copy_args_btn.setText("已复制")
        QTimer.singleShot(1200, lambda: self.td_copy_args_btn.setText("复制"))


def build_tool_detail_page(host, parent, splitter_type):
    """One drill-down page, reusing the application's full-content viewers."""
    import qtawesome as qta
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QToolButton
    from core.theme import DesignTokens, bind_theme
    from ui.primitives import ProductCodeViewer, ProductResultViewer, product_button_style

    page = QWidget(parent)
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)
    head = QHBoxLayout()
    head.setSpacing(8)
    host.td_back_btn = QToolButton(page)
    host.td_back_btn.setFixedSize(32, 32)
    host.td_back_btn.setToolTip("返回调用记录")
    host.td_back_btn.setAccessibleName("返回调用记录")
    host.td_back_btn.clicked.connect(lambda: host.set_observability_section(host.OBS_SECTION_LOG))
    host.td_info_label = QLabel("选择一条调用", page)
    host.td_info_label.setWordWrap(True)
    host.td_info_label.setMinimumWidth(0)
    host.td_status_label = QLabel(page)
    host.td_status_label.setWordWrap(True)
    head.addWidget(host.td_back_btn)
    head.addWidget(host.td_info_label, 1)
    head.addWidget(host.td_status_label)
    layout.addLayout(head)
    host.td_empty_action = QPushButton("查看调用记录", page)
    host.td_empty_action.clicked.connect(lambda: host.set_observability_section(host.OBS_SECTION_LOG))
    layout.addWidget(host.td_empty_action, 0, Qt.AlignLeft | Qt.AlignTop)

    host.td_detail_splitter = splitter_type(Qt.Vertical, page)
    host.td_detail_splitter.setChildrenCollapsible(False)
    host.td_args_section = QWidget(host.td_detail_splitter)
    args_layout = QVBoxLayout(host.td_args_section)
    args_layout.setContentsMargins(0, 0, 0, 0)
    args_layout.setSpacing(8)
    args_head = QHBoxLayout()
    host.td_args_toggle = QToolButton(host.td_args_section)
    host.td_args_toggle.setText("参数")
    host.td_args_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    host.td_args_toggle.setCheckable(True)
    host.td_args_toggle.setChecked(True)
    host.td_copy_args_btn = QPushButton("复制", host.td_args_section)
    host.td_copy_args_btn.setAccessibleName("复制完整参数")
    host.td_copy_args_btn.clicked.connect(host.copy_tool_arguments)
    args_head.addWidget(host.td_args_toggle)
    args_head.addStretch()
    args_head.addWidget(host.td_copy_args_btn)
    args_layout.addLayout(args_head)
    host.td_args_body = QWidget(host.td_args_section)
    body_layout = QVBoxLayout(host.td_args_body)
    body_layout.setContentsMargins(0, 0, 0, 0)
    body_layout.setSpacing(8)
    host.td_args_edit = ProductCodeViewer("json", host.td_args_body)
    host.td_args_meta_label = QLabel("其他参数", host.td_args_body)
    host.td_args_meta_edit = ProductCodeViewer("json", host.td_args_body)
    host.td_args_meta_edit.setMaximumHeight(150)
    for widget in (host.td_args_edit, host.td_args_meta_label, host.td_args_meta_edit):
        body_layout.addWidget(widget)
    host.td_args_meta_label.hide()
    host.td_args_meta_edit.hide()
    args_layout.addWidget(host.td_args_body)
    host.td_args_toggle.toggled.connect(host._set_tool_arguments_expanded)

    result = QWidget(host.td_detail_splitter)
    result_layout = QVBoxLayout(result)
    result_layout.setContentsMargins(0, 0, 0, 0)
    result_layout.setSpacing(8)
    result_head = QHBoxLayout()
    result_label = QLabel("结果", result)
    host.td_copy_result_btn = QPushButton("复制", result)
    host.td_copy_result_btn.setAccessibleName("复制完整结果")
    host.td_copy_result_btn.clicked.connect(host.copy_tool_result)
    result_head.addWidget(result_label)
    result_head.addStretch()
    result_head.addWidget(host.td_copy_result_btn)
    result_layout.addLayout(result_head)
    host.td_result_edit = ProductResultViewer(parent=result)
    result_layout.addWidget(host.td_result_edit, 1)
    for viewer in (host.td_args_edit, host.td_args_meta_edit, host.td_result_edit):
        viewer.set_header_visible(False)
    host.td_detail_splitter.addWidget(host.td_args_section)
    host.td_detail_splitter.addWidget(result)
    host.td_detail_splitter.setStretchFactor(0, 0)
    host.td_detail_splitter.setStretchFactor(1, 1)
    host.td_detail_splitter.setSizes([130, 350])
    layout.addWidget(host.td_detail_splitter, 1)
    host.td_detail_splitter.hide()
    host.td_info_toggle = QToolButton(page)
    host.td_info_toggle.setText("运行信息")
    host.td_info_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    host.td_info_toggle.setCheckable(True)
    host.td_meta_label = QLabel(page)
    host.td_meta_label.setWordWrap(True)
    host.td_meta_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    host.td_meta_label.hide()
    host.td_info_toggle.toggled.connect(host.td_meta_label.setVisible)
    layout.addWidget(host.td_info_toggle, 0, Qt.AlignLeft)
    layout.addWidget(host.td_meta_label)

    def theme(_=None):
        host.td_back_btn.setIcon(qta.icon("fa5s.arrow-left", color=DesignTokens.text_secondary))
        for button in (host.td_args_toggle, host.td_info_toggle):
            button.setIcon(qta.icon("fa5s.chevron-down" if button.isChecked() else "fa5s.chevron-right", color=DesignTokens.text_secondary))
        for button in (host.td_back_btn, host.td_args_toggle, host.td_info_toggle,
                       host.td_copy_args_btn, host.td_copy_result_btn):
            style = product_button_style("ghost")
            if isinstance(button, QToolButton):
                style = style.replace("QPushButton", "QToolButton")
                style += "QToolButton {padding:0 6px;}"
                if button is host.td_back_btn:
                    style += "QToolButton {padding:0;min-height:0;}"
            button.setStyleSheet(style)
        for button in (host.td_copy_args_btn, host.td_copy_result_btn):
            button.setIcon(qta.icon("fa5s.copy", color=DesignTokens.text_secondary))
        host.td_empty_action.setStyleSheet(product_button_style("secondary"))
        host.td_info_label.setStyleSheet(f"color:{DesignTokens.text_primary};font-size:{DesignTokens.font_size_section}px;font-weight:600;background:transparent;")
        for label in (host.td_status_label, host.td_meta_label, host.td_args_meta_label):
            label.setStyleSheet(f"color:{DesignTokens.text_secondary};font-size:{DesignTokens.font_size_meta}px;background:transparent;")
        result_label.setStyleSheet(f"color:{DesignTokens.text_primary};font-weight:600;background:transparent;")

    host.td_info_toggle.toggled.connect(theme)
    host.td_args_toggle.toggled.connect(theme)
    bind_theme(page, theme, surface="right_sidebar")
    return page
