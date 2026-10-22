"""Native team collaboration. Shared facts come from the center; tasks stay local."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import copy
import json
import logging
from pathlib import Path
import re
import threading
import time
import uuid
from urllib.parse import urlsplit

import qtawesome as qta
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from shiboken6 import isValid
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox,
    QDialog, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QLineEdit, QMenu, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from core.team_spaces import TeamSpaceClient
from core.team_space_storage import TeamSpaceError, TeamSpacePaused, checksum
from core.theme import DesignTokens, bind_theme
from ui.primitives import ProductEmptyState, ProductMessageBox as QMessageBox, product_button_style, product_field_style, product_segmented_style
from ui.team_space_widgets import SpaceActionRow, SpaceSkeleton


ROLES = {"owner": "所有者", "admin": "管理员", "member": "成员"}
STATES = {"active": "有效", "archived": "已归档", "pending": "待处理", "in_progress": "进行中",
    "completed": "已完成", "withdrawn": "已撤回", "accepted": "已接受", "declined": "已拒绝",
    "revoked": "已撤销", "expired": "已过期", "running": "阶段性成果", "stopped": "已停止",
    "failed": "未完成", "manual": "人工整理"}
EVENTS = {"space.created": "创建空间", "settings.updated": "修改设置", "space.archive": "归档空间",
    "space.restore": "恢复空间", "space.transfer": "转移所有权", "space.leave": "退出空间",
    "member.remove": "移除成员", "member.role": "修改成员角色", "invite.create": "发出邀请",
    "invite.revoke": "撤销邀请", "invite.accepted": "接受邀请", "invite.declined": "拒绝邀请",
    "plan.create": "创建计划", "plan.update": "修改计划", "plan.status": "更新计划状态",
    "comment.create": "发布留言", "comment.update": "修改留言", "comment.withdraw": "撤回留言",
    "asset.published": "发布资料", "asset.update": "整理资料", "asset.withdraw": "撤下资料",
    "asset.restore": "恢复资料", "folder.create": "新建文件夹", "share.publish": "发布成果",
    "share.withdraw": "撤回成果", "owner.recovered": "恢复空间所有权"}

log = logging.getLogger(__name__)

WRITE_ACTIONS = {"新建计划", "发布留言", "发布留言 / 要求重新打开", "开始任务", "基于计划开始任务",
    "基于此成果开始任务", "上传资料", "上传新版本", "新建文件夹", "发布成果", "发布新快照",
    "修改计划", "更新状态 / 标记完成", "整理资料", "恢复资料", "邀请成员", "保存设置", "新建任务",
    "选择内容发布为成果", "在本机开始任务", "更新状态", "撤回成果", "撤下资料", "确认撤回", "修改留言", "撤回留言",
    "归档空间", "恢复空间", "转移所有权", "移除所选成员", "切换所选成员的管理员角色", "退出空间", "发送邀请", "撤销所选邀请"}
ARCHIVED_WRITE_ACTIONS = {"恢复空间", "转移所有权", "移除所选成员", "退出空间", "撤销所选邀请"}


def stamp(value):
    return datetime.fromtimestamp(value).strftime("%m-%d %H:%M") if value else "—"


class _Bridge(QObject):
    done = Signal(object, object, object)
    progress = Signal(object, str)


class _SignInState(ProductEmptyState):
    def refresh_theme(self, _resolved=None):
        super().refresh_theme(_resolved)
        self.title_label.setStyleSheet(
            f"font-size:{DesignTokens.font_size_page}px;font-weight:{DesignTokens.font_weight_semibold};color:{DesignTokens.text_primary};")
        self.description_label.setStyleSheet(
            f"font-size:{DesignTokens.font_size_body}px;color:{DesignTokens.text_secondary};")
        self.icon_label.setPixmap(qta.icon("fa5s.users", color=DesignTokens.primary).pixmap(24, 24))


class TeamSpacesPage(QDialog):
    taskRequested = Signal(object)
    conversationRequested = Signal(str)

    def __init__(self, host, *, client_factory=TeamSpaceClient):
        super().__init__(host)
        self.setObjectName("TeamSpacesPage")
        self.host, self.client_factory = host, client_factory
        self.client = client_factory()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="team-space-ui")
        self.bridge = _Bridge(self)
        self.bridge.done.connect(self._done)
        self.bridge.progress.connect(self._progress)
        self.destroyed.connect(lambda: self.pool.shutdown(wait=False, cancel_futures=True))
        self.space, self.route, self.ticket = None, ("list", {}), 0
        self.busy, self.dirty, self.draft_key, self.fields = False, False, "", {}
        self._read_busy = False
        self._request_id = 0
        self._active_future = None
        self._cache, self._spaces, self._view_states, self._last_tabs = {}, {}, {}, {}
        self._tab_routes = {}
        self._summaries, self._folder_catalog = {}, {}
        self._history = []
        self._view_signature = None
        self._data_ready = False
        self._offline = False
        self._denied = set()
        self._tab = "plans"
        self._restoring = False
        self._folder_path = []
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_callback = None
        self._search_timer.timeout.connect(lambda: self._search_callback() if self._search_callback else None)
        self.cancel = threading.Event()
        self.failures = 0
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._poll)
        outer = QVBoxLayout(self)
        self.outer_layout = outer
        outer.setContentsMargins(24, 16, 24, 16)
        outer.setSpacing(12)
        self.identity_label = QLabel()
        self.identity_label.setTextFormat(Qt.PlainText)
        self.identity_label.setWordWrap(True)
        self.identity_label.setMinimumWidth(0)
        self.toolbar = SpaceActionRow(self)
        self.back_button = self.button("团队空间", self.open_list, kind="ghost")
        self.toolbar.addWidget(self.back_button)
        self.toolbar.addWidget(self.identity_label, 1)
        self.list_more = self.menu_button("更多", [("打开空间链接", self.open_link), ("刷新空间列表", self.refresh)])
        self.toolbar.addWidget(self.list_more)
        outer.addWidget(self.toolbar)
        self.space_header = QWidget(self)
        self.space_header.setObjectName("TeamSpaceHeader")
        self.space_header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        head = QVBoxLayout(self.space_header)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(12)
        title_row = SpaceActionRow()
        title_block = QWidget()
        title_layout = QVBoxLayout(title_block)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(4)
        self.space_title = QLabel()
        self.space_title.setTextFormat(Qt.PlainText)
        self.space_title.setWordWrap(True)
        self.space_title.setProperty("spaceTextRole", "heading")
        self.space_subtitle = QLabel()
        self.space_subtitle.setTextFormat(Qt.PlainText)
        self.space_subtitle.setWordWrap(True)
        self.space_subtitle.setProperty("spaceTextRole", "secondary")
        title_layout.addWidget(self.space_title)
        title_layout.addWidget(self.space_subtitle)
        title_row.addWidget(title_block, 1)
        utilities = QWidget()
        utility_layout = QHBoxLayout(utilities)
        utility_layout.setContentsMargins(0, 0, 0, 0)
        utility_layout.setSpacing(4)
        self.my_tasks_button = self.button("我的任务", self.my_tasks, kind="ghost")
        self.members_button = self.button("成员", self.members, kind="ghost")
        self.space_more = self.menu_button("更多", [("空间设置", lambda: self.settings(self.space)),
            ("复制空间链接", self.copy_link), ("刷新共享内容", self.refresh)])
        for button in (self.my_tasks_button, self.members_button, self.space_more):
            utility_layout.addWidget(button)
        title_row.addWidget(utilities)
        head.addWidget(title_row)
        self.tab_row = QWidget()
        tabs = QHBoxLayout(self.tab_row)
        tabs.setContentsMargins(0, 0, 0, 0)
        tabs.setSpacing(4)
        self.tab_buttons = {}
        for key, label in [("plans", "计划"), ("assets", "资料"), ("tasks", "成果"), ("activity", "动态")]:
            button = self.button(label, lambda key=key: self._open_tab(key))
            button.setCheckable(True)
            button.setProperty("spaceTab", True)
            self.tab_buttons[key] = button
            tabs.addWidget(button)
        tabs.addStretch()
        head.addWidget(self.tab_row)
        outer.addWidget(self.space_header)
        self.space_header.hide()
        self.status = QLabel("正在连接企业中心…")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status_row = QWidget(self)
        self.status_row.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        status_layout = QHBoxLayout(self.status_row)
        status_layout.setContentsMargins(0, 0, 0, 0)
        self.loading_indicator = QProgressBar()
        self.loading_indicator.setObjectName("TeamSpaceLoading")
        self.loading_indicator.setRange(0, 0)
        self.loading_indicator.setTextVisible(False)
        self.loading_indicator.setFixedSize(56, 4)
        self.loading_indicator.setAccessibleName("正在加载")
        self.loading_indicator.hide()
        status_layout.addWidget(self.loading_indicator, 0, Qt.AlignVCenter)
        status_layout.addWidget(self.status, 1)
        self.refresh_button = self.button("重试", self.refresh, kind="ghost")
        self.refresh_button.hide()
        status_layout.addWidget(self.refresh_button)
        outer.addWidget(self.status_row)
        self.cancel_button = self.button("取消传输", self.cancel.set)
        self.cancel_button.hide()
        outer.addWidget(self.cancel_button, 0, Qt.AlignLeft)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("TeamSpaceScroll")
        self.scroll.viewport().setObjectName("TeamSpaceViewport")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(self.scroll, 1)
        self._reset()
        bind_theme(self, self.apply_theme)
        self.back_shortcut = QShortcut(QKeySequence("Alt+Left"), self)
        self.back_shortcut.activated.connect(lambda: self._call(self.go_back))

    def apply_theme(self, *_):
        self.setStyleSheet(
            f"QDialog#TeamSpacesPage, QWidget#TeamSpaceContent, QWidget#TeamSpaceViewport "
            f"{{background:{DesignTokens.bg_main};color:{DesignTokens.text_primary};}} "
            f"QScrollArea#TeamSpaceScroll {{background:{DesignTokens.bg_main};border:none;}}")
        self.identity_label.setStyleSheet(f"color:{DesignTokens.text_secondary};font-size:{DesignTokens.font_size_meta}px;")
        self.status.setStyleSheet(f"color:{DesignTokens.text_secondary};font-size:{DesignTokens.font_size_meta}px;")
        self.loading_indicator.setStyleSheet(
            f"QProgressBar#TeamSpaceLoading {{background:{DesignTokens.bg_secondary};border:none;border-radius:2px;}}"
            f"QProgressBar#TeamSpaceLoading::chunk {{background:{DesignTokens.primary};border-radius:2px;}}")
        for label in self.findChildren(QLabel):
            if label.property("spaceTextRole"):
                self._style_label(label)
        for widget in self.findChildren(QPushButton):
            if widget.objectName() == "PrimaryBtn":
                continue  # ProductEmptyState owns its primary action theme.
            widget.setStyleSheet(product_segmented_style() if widget.property("spaceTab") else product_button_style(widget.property("spaceKind") or "secondary"))
            if widget.property("spaceTab"):
                widget.setStyleSheet(widget.styleSheet() + f"QPushButton:checked {{border-bottom:2px solid {DesignTokens.primary};border-radius:0;}}")
        for cls in (QLineEdit, QPlainTextEdit, QComboBox):
            for widget in self.findChildren(cls):
                widget.setStyleSheet(product_field_style())
        for tree in self.findChildren(QTreeWidget):
            tree.setStyleSheet(f"QTreeWidget#SpaceTable {{background:{DesignTokens.bg_main}; color:{DesignTokens.text_primary}; border:none;}} "
                f"QTreeWidget#SpaceTable::item {{padding:10px 6px;border-bottom:1px solid {DesignTokens.border_subtle};}} "
                f"QTreeWidget#SpaceTable::item:selected {{background:{DesignTokens.primary_soft};color:{DesignTokens.text_primary};}} "
                f"QTreeWidget#SpaceTable::item:hover {{background:{DesignTokens.bg_hover};}} "
                f"QTreeWidget#SpaceTable QHeaderView::section {{background:{DesignTokens.bg_main};color:{DesignTokens.text_secondary};border:none;border-bottom:1px solid {DesignTokens.border_subtle};padding:8px 6px;}}")
        for menu in self.findChildren(QMenu):
            menu.setStyleSheet(f"QMenu#SpaceMenu {{background:{DesignTokens.bg_main};color:{DesignTokens.text_primary};border:1px solid {DesignTokens.border_subtle};padding:4px;}} "
                f"QMenu#SpaceMenu::item {{padding:8px 18px;}} QMenu#SpaceMenu::item:selected {{background:{DesignTokens.bg_hover};}} "
                f"QMenu#SpaceMenu::item:disabled {{color:{DesignTokens.text_disabled};}}")
        if self.sign_in_state is not None:
            for label in self.sign_in_state.findChildren(QLabel):
                if label.property("spaceIdentity"):
                    label.setStyleSheet(f"color:{DesignTokens.text_secondary};font-size:{DesignTokens.font_size_meta}px;")
        self._fit_tables()

    def button(self, label, callback, primary=False, *, kind=None):
        button = QPushButton(label)
        # This page is hosted in a QDialog, but Enter belongs to the active
        # search/form field, not an unrelated navigation button.
        button.setAutoDefault(False)
        kind = kind or ("primary" if primary else "secondary")
        button.setProperty("spaceKind", kind)
        button.setStyleSheet(product_button_style(kind))
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(lambda checked=False: self._call(callback))
        button.setProperty("spaceWrite", label in WRITE_ACTIONS)
        button.setProperty("spaceArchivedAllowed", label in ARCHIVED_WRITE_ACTIONS)
        self._enable_write(button)
        return button

    def menu_button(self, label, actions):
        button = self.button(label, lambda: None, kind="ghost")
        menu = QMenu(button)
        menu.setObjectName("SpaceMenu")
        for text, callback in actions:
            action = menu.addAction(text)
            action.setProperty("spaceWrite", text in WRITE_ACTIONS)
            action.setProperty("spaceArchivedAllowed", text in ARCHIVED_WRITE_ACTIONS)
            self._enable_write(action)
            action.triggered.connect(lambda checked=False, callback=callback: self._call(callback))
        button.setMenu(menu)
        return button

    def _enable_write(self, widget):
        if not widget.property("spaceWrite"):
            return
        unavailable = getattr(self, "_offline", False) or (self.space and self.space["id"] in self._denied)
        archived = self.space and self.space["state"] == "archived" and not widget.property("spaceArchivedAllowed")
        unready = self.space and "members" not in self.space
        widget.setEnabled(not (unavailable or archived or unready or (self.busy and not self._read_busy)))
        widget.setToolTip("连接恢复并核对权限后可继续；当前内容已保留。" if unavailable else
                          "空间已归档；恢复后可继续编辑和创建任务。" if archived else "")

    def _update_writes(self):
        for widget in self.findChildren(QPushButton) + self.findChildren(QAction):
            self._enable_write(widget)

    def _call(self, callback):
        try:
            if self.busy and not self._read_busy and callback != self.cancel.set:
                return
            callback()
        except Exception as error:
            log.warning("team_space.ui_action_failed route=%s type=%s code=%s", self.route[0], type(error).__name__, getattr(error, "code", ""))
            self.status.setText(str(error))

    def _progress(self, context, message):
        client, ticket = context
        if client is self.client and ticket == self.ticket:
            self.status.setText(message)

    def _reset(self, title="", subtitle=""):
        self._cancel_read()
        self.ticket += 1
        self._search_timer.stop()
        self.sign_in_state = None
        self.identity_label.show()
        self.toolbar.show()
        self.back_button.show()
        self.space_header.setVisible(self.space is not None)
        self.list_more.setVisible(self.space is None)
        self.status_row.show()
        self.status.show()
        self.dirty, self.fields, self.draft_key = False, {}, ""
        self.editing = None
        self.form_extras = {}
        self._data_ready = False
        self._view_signature = None
        body = QWidget()
        body.setObjectName("TeamSpaceContent")
        body.setAttribute(Qt.WA_StyledBackground, True)
        self.layout = QVBoxLayout(body)
        self.layout.setSpacing(12)
        self.layout.setContentsMargins(0, 0, 8, 0)
        # Before an async load completes, labels must not absorb the viewport's
        # spare height. This also keeps short details and read-only forms compact.
        self.layout.setAlignment(Qt.AlignTop)
        old = self.scroll.takeWidget()
        if old:
            old.deleteLater()
        self.scroll.setWidget(body)
        if self.space:
            self.header(self._tab)
        if title:
            self.label(title, role="heading")
        if subtitle:
            self.label(subtitle, role="secondary")

    def _cancel_read(self):
        if self.busy and self._read_busy:
            self._request_id += 1
            if self._active_future is not None:
                self._active_future.cancel()
            self._active_future = None
            self.busy, self._read_busy = False, False
            self.loading_indicator.hide()
            self.refresh_button.setEnabled(True)

    def _show_sign_in(self, *, expired=False):
        state = self.client.enterprise_store.read()
        connection, member = state.get("connection") or {}, state.get("member") or {}
        if not connection:
            title, action = "连接企业，开始团队协作", "设置企业连接"
            description = "与同事共享计划、资料和成果，任务在各自电脑上完成。\n请先在“企业账号”中设置企业连接并登录。"
        elif expired or member:
            title, action = "重新登录企业账号", "前往登录"
            description = "登录状态已失效，登录后即可继续访问团队空间。\n本机任务、已有内容和未提交草稿均已保留。"
        else:
            title, action = "登录后进入团队空间", "前往登录"
            description = "与同事共享计划、资料和成果，任务在各自电脑上完成。\n使用企业员工账号登录，即可创建或加入空间。"
        self._reset()
        self.space = None
        self.space_header.hide()
        self._cache.clear()
        self._spaces.clear()
        self._history.clear()
        self.timer.stop()
        self.identity_label.hide()
        self.toolbar.hide()
        self.status.clear()
        self.status.hide()
        self.status_row.hide()
        self.loading_indicator.hide()
        self.cancel_button.hide()
        empty = _SignInState(title, description, action, appearance="plain",
            icon=qta.icon("fa5s.users", color=DesignTokens.primary))
        empty.setMaximumWidth(560)
        empty.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        empty.title_label.setWordWrap(True)
        empty.description_label.setMaximumWidth(440)
        empty.layout().setAlignment(Qt.AlignTop)
        empty.layout().setAlignment(empty.description_label, Qt.AlignHCenter)
        empty.layout().setSpacing(14)
        empty.icon_label.setFixedSize(48, 48)
        empty.refresh_theme()
        empty.action_button.clicked.connect(lambda: self._call(lambda: self.host.open_settings("企业账号")))
        if connection:
            identity = QLabel("企业：" + (connection.get("platform_name") or "已配置企业"))
            identity.setAlignment(Qt.AlignCenter)
            identity.setWordWrap(True)
            identity.setTextFormat(Qt.PlainText)
            identity.setProperty("spaceIdentity", True)
            empty.layout().insertWidget(1, identity)
            empty.layout().addWidget(self.button("我已登录，刷新", self.refresh), 0, Qt.AlignCenter)
        self.sign_in_state = empty
        self._fit_sign_in_state()
        # The sign-in panel deliberately uses the available height for its
        # surrounding spacers; ordinary content pages are top-aligned.
        self.layout.setAlignment(Qt.AlignmentFlag(0))
        self.layout.addStretch(1)
        self.layout.addWidget(empty, 0, Qt.AlignHCenter)
        self.layout.addStretch(2)
        self.apply_theme()

    def _fit_sign_in_state(self):
        if self.sign_in_state is None:
            return
        margins = self.outer_layout.contentsMargins()
        content_margins = self.layout.contentsMargins()
        width = min(520, self.width() - margins.left() - margins.right()
                    - content_margins.left() - content_margins.right())
        self.sign_in_state.setFixedWidth(max(120, width))
        self.sign_in_state.description_label.setFixedWidth(max(80, min(440, width - 40)))
        self.sign_in_state.setMinimumHeight(self.sign_in_state.layout().totalHeightForWidth(max(120, width)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_sign_in_state()
        self._fit_tables()

    def _style_label(self, label):
        role = label.property("spaceTextRole")
        size = DesignTokens.font_size_page if role == "heading" else DesignTokens.font_size_body
        weight = DesignTokens.font_weight_semibold if role == "heading" else "normal"
        color = DesignTokens.text_secondary if role == "secondary" else DesignTokens.text_primary
        label.setStyleSheet(f"font-size:{size}px;font-weight:{weight};color:{color};")

    def label(self, text, *, role="body"):
        label = QLabel(str(text))
        label.setProperty("spaceTextRole", role)
        label.setTextFormat(Qt.PlainText)
        label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._style_label(label)
        self.layout.addWidget(label)
        return label

    def actions(self, *actions):
        row = SpaceActionRow()
        for action in actions:
            row.addWidget(self.button(*action))
        row.addStretch()
        self.layout.addWidget(row)
        return row

    def table(self, columns, rows, values, activate=None):
        tree = QTreeWidget()
        tree.setObjectName("SpaceTable")
        tree.setColumnCount(len(columns))
        tree.setHeaderLabels(columns)
        tree.setRootIsDecorated(False)
        tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tree.setSelectionMode(QAbstractItemView.SingleSelection)
        tree.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        tree.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        tree.header().setMinimumSectionSize(60)
        tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for index in range(1, len(columns)):
            tree.header().setSectionResizeMode(index, QHeaderView.ResizeToContents)
        self.update_table(tree, rows, values)
        if activate:
            tree.setCursor(Qt.PointingHandCursor)
            def open_item(item, col=0):
                if tree.isVisible():
                    self._call(lambda: activate(item.data(0, Qt.UserRole)))
            tree.itemClicked.connect(open_item)
            tree.itemActivated.connect(open_item)
        self.layout.addWidget(tree)
        return tree

    def update_table(self, tree, rows, values):
        """Reconcile rows by identity; keep current selection, focus and scroll."""
        tree.setUpdatesEnabled(False)
        try:
            old = {str(tree.topLevelItem(i).data(0, Qt.UserRole).get("id", i)): tree.topLevelItem(i)
                   for i in range(tree.topLevelItemCount())}
            selected = tree.currentItem()
            selected_id = (selected.data(0, Qt.UserRole) or {}).get("id") if selected else None
            for index, row in enumerate(rows):
                key = str(row.get("id", index))
                item = old.pop(key, None)
                if item is None:
                    item = QTreeWidgetItem()
                    tree.insertTopLevelItem(index, item)
                elif tree.indexOfTopLevelItem(item) != index:
                    tree.takeTopLevelItem(tree.indexOfTopLevelItem(item))
                    tree.insertTopLevelItem(index, item)
                for column, value in enumerate(values(row)):
                    value = str(value)
                    if item.text(column) != value:
                        item.setText(column, value)
                    item.setToolTip(column, value)
                item.setData(0, Qt.UserRole, row)
                if row.get("id") == selected_id:
                    tree.setCurrentItem(item)
            for item in old.values():
                tree.takeTopLevelItem(tree.indexOfTopLevelItem(item))
        finally:
            tree.setUpdatesEnabled(True)
        self._fit_table_height(tree)
        self._fit_tables()

    @staticmethod
    def _fit_table_height(tree):
        # QSS changes both header padding and row height. Lay out with the
        # current style before sizing the view, or the final row is clipped.
        tree.ensurePolished()
        tree.header().ensurePolished()
        tree.doItemsLayout()
        tree.setFixedHeight(max(88, tree.header().sizeHint().height() +
            sum(max(48, tree.sizeHintForRow(i)) for i in range(tree.topLevelItemCount())) +
            tree.frameWidth() * 2 + 4))

    def _fit_tables(self):
        for tree in self.findChildren(QTreeWidget):
            for index in range(2, tree.columnCount()):
                tree.setColumnHidden(index, self.width() < 620)
            self._fit_table_height(tree)

    def _route_key(self, route=None, space_id=None):
        name, args = route or self.route
        space_id = space_id if space_id is not None else (self.space or {}).get("id", "")
        return (space_id, name, json.dumps(args, sort_keys=True, ensure_ascii=False))

    def _save_view(self):
        key = getattr(self, "_display_key", None)
        if key is None:
            return
        selected = []
        for tree in self.scroll.widget().findChildren(QTreeWidget):
            item = tree.currentItem()
            selected.append((item.data(0, Qt.UserRole) or {}).get("id") if item else None)
        self._view_states[key] = (self.scroll.verticalScrollBar().value(), selected)

    def _restore_view(self, key):
        if key != getattr(self, "_display_key", None):
            return
        position, selected = self._view_states.get(key, (0, []))
        for tree, object_id in zip(self.scroll.widget().findChildren(QTreeWidget), selected):
            for index in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(index)
                if item.data(0, Qt.UserRole).get("id") == object_id:
                    tree.setCurrentItem(item)
        self.scroll.verticalScrollBar().setValue(position)

    def _begin_view(self, route, signature, *, title="", subtitle="", push=False):
        if self.busy and not self._read_busy:
            return None
        if self.busy and self._read_busy and self.route == route:
            return None
        if not self.confirm_leave():
            return None
        try:
            self.client.session()
        except TeamSpaceError:
            self.refresh()
            return None
        self._save_view()
        self._route_params_changed = self.route != route
        if push and not self._restoring and self.route != route and self.route[0] != "form":
            self._history.append(copy.deepcopy(self.route))
        new_page = self._view_signature != signature
        self.route = route
        if new_page:
            self._reset(title, subtitle)
            self._view_signature = signature
        else:
            self._cancel_read()
            self.ticket += 1
        key = self._route_key()
        if key != getattr(self, "_display_key", None):
            self._data_ready = False
        self._display_key = key
        return new_page

    def go_back(self):
        if not self.confirm_leave():
            return
        self.dirty = False
        if not self._history:
            if self.space:
                self.open_space(self.space["id"], self._tab)
            else:
                self.open_list()
            return
        route = self._history.pop()
        self._restoring = True
        self.dirty = False  # confirm_leave above already preserved/confirmed the draft.
        try:
            getattr(self, route[0])(**route[1])
        finally:
            self._restoring = False

    def _back_to_content(self, label="返回列表"):
        self.layout.addWidget(self.button("← " + label, self.go_back, kind="ghost"), 0, Qt.AlignLeft)

    def _read_view(self, work, render, *, background=False):
        key, client = self._display_key, self.client
        cached = self._cache.get(key)
        if cached is not None and not self._data_ready:
            render(copy.deepcopy(cached))
            self._data_ready = True
            QTimer.singleShot(0, lambda: self._restore_view(key))
        def loaded(result):
            changed = self._cache.get(key) != result or not self._data_ready
            self._cache[key] = copy.deepcopy(result)
            while len(self._cache) > 96:
                self._cache.pop(next(iter(self._cache)))
            if changed:
                if self._data_ready:
                    self._save_view()
                render(result)
                self._data_ready = True
                QTimer.singleShot(0, lambda: self._restore_view(key))
            self._offline = False
            self._denied.discard(key[0])
            self._update_writes()
        self.apply_theme()
        self._async(work, loaded, read=True, polling=background,
            loading_message="正在同步最新内容，可继续浏览。" if self._data_ready else "正在读取内容…",
            refresh_error_label="重试")

    def _invalidate_space(self, space_id):
        self._cache = {key: value for key, value in self._cache.items() if key[0] != space_id}
        self._spaces.pop(space_id, None)
        self._folder_catalog.pop(space_id, None)

    @staticmethod
    def selected(tree):
        item = tree.currentItem()
        if not item:
            raise TeamSpaceError("请先选择一项。")
        return item.data(0, Qt.UserRole)

    def _async(self, work, done, *, transfer=False, polling=False,
               loading_message="正在处理，请稍候…", refresh_error_label="刷新", read=False):
        if self.busy and not self._read_busy:
            return
        if self._active_future is not None:
            self._active_future.cancel()
        self._request_id += 1
        request_id = self._request_id
        self.busy = True
        self._read_busy = read
        self.cancel.clear()
        self.cancel_button.setVisible(transfer)
        self.refresh_button.setEnabled(False)
        self.refresh_button.hide()
        self.loading_indicator.hide()
        if not polling:
            self.status.setText(loading_message)
            def indicate():
                if self.busy and self._request_id == request_id:
                    self.loading_indicator.show()
            QTimer.singleShot(200, indicate)
        ticket, client = self.ticket, self.client
        started, route = time.monotonic(), self.route[0]
        self._update_writes()
        log.debug("team_space.ui_request_started request=%s ticket=%s route=%s read=%s", request_id, ticket, route, read)
        def execute():
            try:
                client.session()
                result = work()
                client.session()
                return result, None
            except Exception as error:
                return None, error
        future = self.pool.submit(execute)
        self._active_future = future
        def completed(future):
            if not future.cancelled() and isValid(self.bridge):
                self.bridge.done.emit((request_id, ticket, client, done, polling, refresh_error_label, started, route, read), *future.result())
        future.add_done_callback(completed)

    def _done(self, context, result, error):
        request_id, ticket, client, callback, polling, refresh_error_label, started, route, read = context
        if request_id != self._request_id or ticket != self.ticket or client is not self.client:
            log.debug("team_space.ui_result_discarded request=%s route=%s", request_id, route)
            return
        self.busy = False
        self._read_busy = False
        self._active_future = None
        self.refresh_button.setEnabled(True)
        self.cancel_button.hide()
        self.loading_indicator.hide()
        try:
            client.session()
        except TeamSpaceError:
            self._show_sign_in()
            return
        self._update_writes()
        if error:
            log.warning("team_space.ui_request_failed request=%s route=%s elapsed_ms=%s type=%s code=%s",
                        request_id, route, round((time.monotonic()-started)*1000), type(error).__name__, getattr(error, "code", ""))
            if getattr(error, "status", 0) == 401:
                self._show_sign_in(expired=True)
                return
            if getattr(error, "status", 0) == 403 and self.space:
                self._invalidate_space(self.space["id"])
                self._denied.add(self.space["id"])
                self._reset("暂时无法访问这些共享内容", "权限可能已变化，请重试核对或联系空间管理员。本机任务和草稿仍保留。")
            elif read and getattr(error, "status", 0) in {404, 410}:
                if self.space:
                    self._invalidate_space(self.space["id"])
                else:
                    self._cache.pop(getattr(self, "_display_key", None), None)
                self._reset("此内容已不可用", "内容可能已撤回或删除。已有本机文件和任务仍保留。")
                self._back_to_content("上一页")
            elif read and isinstance(error, TeamSpacePaused):
                self._offline = True
            if getattr(self, "_skeleton", None) is not None and isValid(self._skeleton):
                self._skeleton.hide()
            self.status.setText(str(error) + (" 草稿已保留。" if self.draft_key else "") +
                                (" 当前显示上次加载的内容。" if read and self._data_ready else ""))
            self.refresh_button.setText(refresh_error_label)
            self.refresh_button.show()
            self._update_writes()
            if getattr(error, "status", 0) == 409 and self.draft_key:
                self._conflict()
            if polling:
                self.failures += 1
                if self.failures < 5:
                    self.timer.start(min(60000, 10000 * 2 ** self.failures))
            return
        self.failures = 0
        if read:
            self._offline = False
            self._update_writes()
        log.debug("team_space.ui_request_completed request=%s route=%s elapsed_ms=%s", request_id, route, round((time.monotonic()-started)*1000))
        if not polling:
            self.status.clear()
        self._call(lambda: callback(result))
        self.apply_theme()
        if self.isVisible():
            self.timer.start(10000)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.refresh)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def _poll(self):
        if not self.isVisible() or not self.space or self.busy:
            return
        if self._search_timer.isActive():
            self.timer.start(1000)
            return
        client, space_id = self.client, self.space["id"]
        old = self.space.get("event_version", -1)
        def check():
            data = client.collection(space_id, "activity", after=max(0, old), limit=1)
            return client.detail(space_id) if data["event_version"] != old else None
        def updated(space):
            if space is None:
                return
            self.space = space
            self._spaces[space_id] = copy.deepcopy(space)
            self.header(self._tab)
            if self.draft_key:
                self.status.setText("共享内容有更新；当前输入已保留，提交时会核对版本。")
                self._update_writes()
            else:
                self.refresh()
        self._async(check, updated, polling=True, read=True)

    def confirm_leave(self):
        if self.busy and self.cancel_button.isVisible():
            return QMessageBox.question(self, "传输仍在进行", "离开页面后传输继续，草稿保留。是否离开？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes
        if not self.dirty:
            return True
        return QMessageBox.question(self, "修改尚未提交", "草稿已保存在本机；离开不会提交到中心。是否离开？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def refresh(self):
        fresh = self.client_factory()
        if fresh.identity != self.client.identity or fresh.generation != self.client.generation:
            self._request_id += 1
            self.busy, self._read_busy = False, False
            self.client, self.space, self.route = fresh, None, ("list", {})
            self._cache.clear()
            self._spaces.clear()
            self._view_states.clear()
            self._last_tabs.clear()
            self._tab_routes.clear()
            self._summaries.clear()
            self._folder_catalog.clear()
            self._history.clear()
            self._denied.clear()
            self._offline = False
            self._display_key = None
            self._reset()
        elif self.busy and not self._read_busy:
            return
        state = self.client.enterprise_store.read()
        try:
            self.client.session()
        except TeamSpaceError:
            self._show_sign_in()
            return
        self.identity_label.setText(f"{(state.get('connection') or {}).get('platform_name') or '尚未连接企业'} · {(state.get('member') or {}).get('name') or '未登录'}")
        if self.draft_key:
            self._poll()
            return
        name, args = self.route
        getattr(self, name)(**args)

    def open_list(self):
        self.list()

    def list(self, search="", archived=False, offset=0):
        if not self.confirm_leave():
            return
        self._save_view()
        self.space = None
        self.dirty = False
        new_page = self._begin_view(("list", dict(search=search, archived=archived, offset=offset)),
            ("list", archived), title="已归档空间" if archived else "我的空间", subtitle="选择空间继续协作，或创建空间邀请同事。")
        if new_page is None:
            return
        self._history.clear()
        self.back_button.hide()
        if new_page:
            row = SpaceActionRow()
            query = QLineEdit(search)
            self.list_query = query
            query.setPlaceholderText("搜索已加入空间")
            query.setClearButtonEnabled(True)
            query.setAccessibleName("搜索已加入空间")
            self._bind_search(query, lambda: self.list(query.text(), archived))
            row.addWidget(query, 1)
            actions = QWidget()
            action_layout = QHBoxLayout(actions)
            action_layout.setContentsMargins(0, 0, 0, 0)
            action_layout.setSpacing(8)
            action_layout.addWidget(self.button("活跃空间" if archived else "已归档", lambda: self.list(query.text(), not archived)))
            action_layout.addWidget(self.button("新建空间", lambda: self.settings(None), True))
            row.addWidget(actions)
            self.layout.addWidget(row)
            self.inbox_area = QWidget()
            self.inbox_layout = QVBoxLayout(self.inbox_area)
            self.inbox_layout.setContentsMargins(0, 0, 0, 0)
            self.layout.addWidget(self.inbox_area)
            self._build_list_area(["空间", "本人角色", "共享更新"], lambda r: self.open_space(r["id"]))
        elif self._route_params_changed and self.list_query.text() != search:
            self.list_query.setText(search)
        client = self.client
        def loaded(result):
            data, inbox = result
            self._summaries.update({row["id"]: copy.deepcopy(row) for row in data["items"]})
            self._clear_layout(self.inbox_layout)
            if inbox["items"]:
                previous = self.layout
                self.layout = self.inbox_layout
                self.label("待处理邀请")
                tree = self.table(["空间", "邀请人", "角色", "状态"], inbox["items"], lambda r: [r["space_name"], r["sender_name"], ROLES[r["role"]], STATES[r["state"]]])
                def respond(answer):
                    item = self.selected(tree)
                    self.mutate("invite.respond", {"id": item["id"], "revision": item["revision"], "response": answer}, space_id=item["space_id"], done=lambda _: self.list())
                self.actions(("接受邀请", lambda: respond("accept")), ("拒绝邀请", lambda: respond("decline")))
                self.layout = previous
            empty = ("没有找到匹配的空间", "换个关键词，或清空搜索查看全部空间。") if search else (
                ("没有已归档空间", "归档后的空间会显示在这里，可切回活跃空间继续协作。") if archived else
                ("还没有加入空间", "点击新建空间开始协作，或请同事邀请你。"))
            self._fill_list(data, lambda r: [r["name"], ROLES[r["role"]], stamp(r["updated_at"])],
                empty, offset, lambda next: self.list(search, archived, next))
        self._read_view(lambda: (client.list(search=search, archived=str(archived).lower(), offset=offset),
                                client.request("GET", "/team-space-invites")), loaded)

    @staticmethod
    def _clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                TeamSpacesPage._clear_layout(item.layout())

    def _bind_search(self, query, callback):
        def search_now():
            self._search_timer.stop()
            self._call(callback)
        def debounce():
            self._search_callback = callback
            self._search_timer.start()
        query.returnPressed.connect(search_now)
        query.textEdited.connect(debounce)

    def _build_list_area(self, columns, activate):
        self._skeleton = SpaceSkeleton()
        self.layout.addWidget(self._skeleton)
        self.main_table = self.table(columns, [], lambda _: [], activate)
        self.main_table.hide()
        self.empty_title = self.label("")
        self.empty_description = self.label("", role="secondary")
        self.empty_title.hide()
        self.empty_description.hide()
        self.list_footer = self.label("", role="secondary")
        self.pager_area = QWidget()
        self.pager_layout = QVBoxLayout(self.pager_area)
        self.pager_layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(self.pager_area)

    def _fill_list(self, data, values, empty, offset, page):
        self._skeleton.hide()
        rows = data["items"]
        self.update_table(self.main_table, rows, values)
        self.main_table.setVisible(bool(rows))
        self.empty_title.setText(empty[0])
        self.empty_description.setText(empty[1])
        self.empty_title.setVisible(not rows)
        self.empty_description.setVisible(not rows)
        self.list_footer.setText(f"{offset + 1}–{offset + len(rows)} 项" if offset and rows else f"{len(rows)} 项")
        self._clear_layout(self.pager_layout)
        previous = self.layout
        self.layout = self.pager_layout
        self.pager(data, offset, page)
        self.layout = previous

    def pager(self, data, offset, callback):
        controls = []
        if offset:
            controls.append(("上一页", lambda: callback(max(0, offset - 50))))
        if data.get("has_more"):
            controls.append(("下一页", lambda: callback(offset + 50)))
        if controls:
            self.actions(*controls)

    def open_link(self):
        link, ok = QInputDialog.getText(self, "打开空间链接", "粘贴同一企业平台的空间或计划链接")
        if not ok:
            return
        base = urlsplit(self.client.session()["connection"]["platform_url"])
        parsed = urlsplit(link.strip())
        expected = base.path.rstrip("/") + "/app/team-spaces/"
        if (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc) or not parsed.path.startswith(expected):
            raise TeamSpaceError("链接不属于当前登录的企业平台，请切换正确账号后打开。")
        parts = parsed.path[len(expected):].split("/")
        if not re.fullmatch(r"[a-f0-9]{32}", parts[0]):
            raise TeamSpaceError("空间链接格式无效。")
        self.open_space(parts[0], "plans" if len(parts) > 1 and parts[1] == "plans" else "activity",
                        target=parts[2] if len(parts) == 3 and parts[1] == "plans" else "")

    def open_space(self, space_id, tab=None, offset=0, search="", assignee_id="", state="", folder_id="", target=""):
        if (self.busy and not self._read_busy) or not self.confirm_leave():
            return
        if tab is None:
            saved = self._last_tabs.get(space_id)
            if saved is None:
                stored = self.client.local_store.cached(self.client.identity, space_id, "navigation")
                saved = stored["value"] if stored else {}
            tab = saved.get("tab", "plans")
            offset, search = saved.get("offset", 0), saved.get("search", "")
            assignee_id, state, folder_id = saved.get("assignee_id", ""), saved.get("state", ""), saved.get("folder_id", "")
        if tab not in {"plans", "assets", "tasks", "activity"}:
            tab = "plans"
        self._save_view()
        self.dirty = False
        self._tab = tab
        known = copy.deepcopy(self._spaces.get(space_id))
        if not self.space or self.space["id"] != space_id:
            self.space = known or self._summaries.get(space_id) or {"id": space_id, "name": "团队空间", "description": "", "state": "active", "role": "member"}
        args = dict(space_id=space_id, tab=tab, offset=offset, search=search, assignee_id=assignee_id, state=state, folder_id=folder_id)
        new_page = self._begin_view(("open_space", args), ("collection", space_id, tab))
        if new_page is None:
            return
        if not self._restoring:
            self._history.clear()
        self._last_tabs[space_id] = args
        self._tab_routes[(space_id, tab)] = args
        self.client.local_store.cached(self.client.identity, space_id, "navigation", args)
        self.header(tab)
        if new_page:
            self._collection_controls(tab, args)
        elif self._route_params_changed and tab != "activity":
            self.collection_query.setText(search)
            if tab == "plans":
                selected = self.collection_filter.findData((assignee_id, state))
                self.collection_filter.setCurrentIndex(max(0, selected))
        client = self.client
        filters = {"offset": offset, "search": search}
        if tab == "plans":
            filters.update(assignee_id=assignee_id, state=state)
        if tab == "assets":
            filters["folder_id"] = folder_id
        collection = {"activity": "activity", "plans": "plans", "tasks": "shares", "assets": "assets"}[tab]
        cached = self._cache.get(self._display_key) or {}
        folder_catalog = copy.deepcopy(self._folder_catalog.get(space_id, {}))
        def work():
            space = known or client.detail(space_id)
            data = client.collection(space_id, collection, **filters)
            if data["event_version"] != space["event_version"]:
                space = client.detail(space_id)
            folders = []
            if tab == "assets":
                if folder_catalog.get("event_version") == data["event_version"]:
                    folders = folder_catalog["items"]
                elif cached.get("data", {}).get("event_version") == data["event_version"]:
                    folders = cached.get("folders", [])
                else:
                    for page in range(0, 10000, 100):
                        batch = client.collection(space_id, "folders", limit=100, offset=page)
                        folders.extend(batch["items"])
                        if not batch["has_more"]:
                            break
                    else:
                        raise TeamSpaceError("文件夹超过一万项，请先整理空间内容。")
            return {"space": space, "data": data, "folders": folders}
        def loaded(result):
            latest = self._spaces.get(space_id)
            self.space = latest if latest and latest["event_version"] > result["space"]["event_version"] else result["space"]
            self._spaces[space_id] = copy.deepcopy(self.space)
            self.header(tab)
            data = result["data"]
            rows = data["items"]
            if tab == "assets":
                folders = result["folders"]
                self._folder_catalog[space_id] = {"event_version": data["event_version"], "items": copy.deepcopy(folders)}
                self._show_folder_path(folders, folder_id, space_id)
                children = [dict(f, _folder=True) for f in folders if f["parent_id"] == folder_id and search in f["name"]] if not offset else []
                data = {**data, "items": children + rows}
                values = lambda r: ["文件夹 · " + r["name"] if r.get("_folder") else r["name"] + f"\n第 {r['latest']} 版 · {STATES[r['state']]}",
                    "—" if r.get("_folder") else r["author_name"], stamp(r.get("updated_at"))]
            elif tab == "plans":
                values = lambda r: [r["title"] + ("\n高优先级" if r["priority"] == "high" else ""), STATES[r["state"]],
                    r["assignee_name"] + ("（已不可用）" if r["assignee_id"] and not r["assignee_available"] else "")]
            elif tab == "tasks":
                values = lambda r: [r["title"] + f"\n第 {r['latest']} 个快照", r["author_name"], STATES[r["state"]]]
            else:
                values = lambda r: [EVENTS.get(r["kind"], "空间更新"), r["actor_name"], stamp(r["created_at"])]
            empty = ("没有找到匹配内容", "换个关键词或筛选条件。") if search or assignee_id or state else {
                "plans": ("一起明确第一件要完成的事", "点击新建计划，填写任务要求并指定负责人。"),
                "assets": ("还没有共享资料", "上传需求、参考文档或交付文件，让同事可以一起使用。"),
                "tasks": ("还没有发布成果", "可从我的任务选择内容分享，也可以直接发布人工整理的成果。"),
                "activity": ("还没有新的动态", "计划、资料和成果的变化会显示在这里。")}[tab]
            self._fill_list(data, values, empty, offset, lambda next: self.open_space(**{**args, "offset": next}))
            if target:
                QTimer.singleShot(0, lambda: self.open_object("plan", target))
        self._read_view(work, loaded)

    def _open_tab(self, tab):
        space_id = self.space["id"]
        self.open_space(**self._tab_routes.get((space_id, tab), {"space_id": space_id, "tab": tab}))

    def _collection_controls(self, tab, args):
        space_id = args["space_id"]
        row = SpaceActionRow()
        if tab != "activity":
            query = QLineEdit(args["search"])
            self.collection_query = query
            query.setClearButtonEnabled(True)
            query.setAccessibleName("搜索当前内容")
            query.setPlaceholderText("搜索" + {"plans": "计划", "assets": "当前文件夹中的资料", "tasks": "成果"}[tab])
            self._bind_search(query, lambda: self.open_space(**{**self.route[1], "search": query.text(), "offset": 0}))
            row.addWidget(query, 1)
        if tab == "plans":
            choices = [("全部计划", ("", "")), ("我负责的", (self.client.identity["member_id"], "")),
                       ("待处理", ("", "pending")), ("进行中", ("", "in_progress")), ("已完成", ("", "completed"))]
            current = (args["assignee_id"], args["state"])
            if current not in [value for _, value in choices]:
                choices.append(("当前筛选", current))
            box = self.combo(choices, current)
            self.collection_filter = box
            box.setAccessibleName("计划筛选")
            box.activated.connect(lambda: self.open_space(**{**self.route[1], "offset": 0,
                "assignee_id": box.currentData()[0], "state": box.currentData()[1]}))
            row.addWidget(box)
            row.addWidget(self.button("新建计划", self.plan_form, True))
            columns, activate = ["计划", "状态", "负责人"], lambda r: self.open_object("plan", r["id"])
        elif tab == "assets":
            row.addWidget(self.button("上传资料", lambda: self.upload_form(folder_id=self.route[1].get("folder_id", "")), True))
            row.addWidget(self.menu_button("更多", [("新建文件夹", lambda: self.folder_form(self.route[1].get("folder_id", "")))]))
            columns = ["名称", "更新人", "更新时间"]
            activate = lambda r: self.open_space(space_id, "assets", folder_id=r["id"]) if r.get("_folder") else self.open_object("asset", r["id"])
        elif tab == "tasks":
            row.addWidget(self.button("发布成果", self.share_form, True))
            columns, activate = ["成果", "发布人", "状态"], lambda r: self.open_object("share", r["id"])
        else:
            row.addWidget(self.button("发布留言", lambda: self.comment_form("space", space_id), True))
            row.addWidget(self.button("查看讨论", lambda: self.comments("space", space_id), kind="ghost"))
            row.addStretch()
            columns, activate = ["动态", "成员", "时间"], self.open_event
        self.layout.addWidget(row)
        if tab == "assets":
            self.folder_crumb = QWidget()
            self.folder_crumb_layout = QVBoxLayout(self.folder_crumb)
            self.folder_crumb_layout.setContentsMargins(0, 0, 0, 0)
            self.layout.addWidget(self.folder_crumb)
        self._build_list_area(columns, activate)

    def _show_folder_path(self, folders, folder_id, space_id):
        self._clear_layout(self.folder_crumb_layout)
        by_id, path, seen = {f["id"]: f for f in folders}, [], set()
        while folder_id in by_id and folder_id not in seen:
            seen.add(folder_id)
            folder = by_id[folder_id]
            path.append(folder)
            folder_id = folder["parent_id"]
        row = SpaceActionRow()
        row.addWidget(self.button("全部资料", lambda: self.open_space(space_id, "assets"), kind="ghost"))
        for folder in reversed(path):
            row.addWidget(self.button("/ " + folder["name"], lambda folder=folder: self.open_space(space_id, "assets", folder_id=folder["id"]), kind="ghost"))
        row.addStretch()
        self.folder_crumb_layout.addWidget(row)

    def header(self, tab=""):
        if not self.space:
            self.space_header.hide()
            return
        self.space_header.show()
        self.space_title.setText(self.space["name"])
        self.space_subtitle.setText("已归档 · 共享内容只读" if self.space["state"] == "archived" else self.space.get("description") or "与同事共享计划、资料和成果")
        for key, button in self.tab_buttons.items():
            selected = key == tab and self.route[0] == "open_space"
            button.setChecked(selected)
            button.setAccessibleName(button.text() + ("，当前页" if selected else ""))
        ready = "members" in self.space
        self.members_button.setEnabled(ready)
        self.space_more.setEnabled(ready)
        self.my_tasks_button.setEnabled(True)
        self.list_more.hide()

    def my_tasks(self):
        if not self.space:
            return
        space_id = self.space["id"]
        new_page = self._begin_view(("my_tasks", {}), ("my_tasks", space_id), title="我的任务",
            subtitle="仅本人在这台电脑上可见。完整对话不会自动共享。", push=True)
        if new_page is None:
            return
        if new_page:
            self._back_to_content("空间")
            self.actions(("新建任务", self.start_form, True))
            self._build_list_area(["本机任务", "最近使用"], lambda r: self.local_task(r["id"]))
        tasks = self.host.chat_storage.list_team_space_conversations(**self.client.identity, space_id=space_id)
        self._fill_list({"items": tasks}, lambda r: [r["title"], stamp(r["updated_at"])],
            ("还没有本机任务", "从计划开始工作，或点击新建任务。已有本机任务请在原电脑打开。"), 0, lambda _: None)
        self._data_ready = True
        self.header("")
        self._update_writes()
        self.apply_theme()

    def local_task(self, task_id):
        tasks = self.host.chat_storage.list_team_space_conversations(**self.client.identity, space_id=self.space["id"])
        task = next((r for r in tasks if r["id"] == task_id), None)
        if task is None:
            raise TeamSpaceError("此本机任务已不可用，请返回我的任务刷新。")
        if not self._begin_view(("local_task", {"task_id": task_id}), ("local_task", task_id), title=task["title"], subtitle="本机任务 · 仅本人可见", push=True):
            return
        self._back_to_content("我的任务")
        self.actions(("继续对话", lambda: self.conversationRequested.emit(task_id), True),
                     ("选择内容发布为成果", lambda: self.share_form(task_id=task_id)))
        self.header("")
        self.apply_theme()

    def copy_link(self):
        link = self.client.session()["connection"]["platform_url"] + "/app/team-spaces/" + self.space["id"] + "/activity"
        QApplication.clipboard().setText(link)
        self.status.setText("空间链接已复制；链接本身不授予访问权限。")

    def open_event(self, item):
        if item["target_kind"] in {"plan", "share", "asset", "comment"}:
            self.open_object(item["target_kind"], item["target_id"])
        else:
            self.status.setText(EVENTS.get(item["kind"], "空间更新") + " · " + stamp(item["created_at"]))

    def combo(self, options, selected=""):
        box = QComboBox()
        for label, value in options:
            box.addItem(label, value)
        box.setCurrentIndex(max(0, box.findData(selected)))
        return box

    def form(self, title, key, definitions, values=None, *, advanced=()):
        if not self.confirm_leave():
            return False
        self._save_view()
        if self.route[0] != "form":
            self._history.append(copy.deepcopy(self.route))
        self.route = ("form", {"key": key})
        self._reset(title)
        self._back_to_content("上一页")
        self.draft_key = key
        self.form_space_id = self.space["id"] if self.space else ""
        saved = self.client.local_store.draft(self.client.identity, self.form_space_id, key) or {}
        self.form_extras = copy.deepcopy(saved.get("extras", {}))
        values = {**(values or {}), **saved.get("values", {})}
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        advanced_box = QWidget()
        advanced_form = QFormLayout(advanced_box)
        advanced_form.setContentsMargins(0, 8, 0, 8)
        advanced_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        advanced_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        advanced_box.hide()
        for name, label, kind in definitions:
            value = values.get(name, "")
            if kind == "text":
                widget = QPlainTextEdit(str(value))
                widget.setMinimumHeight(110)
                widget.setMaximumHeight(240)
                widget.textChanged.connect(self._save_draft)
            elif kind == "bool":
                widget = QCheckBox(label)
                widget.setChecked(bool(value))
                widget.toggled.connect(self._save_draft)
                label = ""
            elif isinstance(kind, list):
                widget = self.combo(kind, value)
                widget.currentIndexChanged.connect(self._save_draft)
            else:
                widget = QLineEdit(str(value))
                widget.textChanged.connect(self._save_draft)
            widget.setObjectName("SpaceField_" + name)
            self.fields[name] = widget
            (advanced_form if name in advanced else form).addRow(label, widget)
        self.layout.addLayout(form)
        if advanced:
            toggle = self.button("模型与技能规则 ▸", lambda: None, kind="ghost")
            toggle.setCheckable(True)
            def expand(checked):
                advanced_box.setVisible(checked)
                toggle.setText("模型与技能规则 ▾" if checked else "模型与技能规则 ▸")
            toggle.toggled.connect(expand)
            self.layout.addWidget(toggle, 0, Qt.AlignLeft)
            self.layout.addWidget(advanced_box)
        else:
            advanced_box.deleteLater()
        self.advanced_form = advanced_form if advanced else None
        self.dirty = bool(saved.get("values"))
        if self.dirty:
            self.label("已恢复本机草稿；点击提交后才会与成员共享。")
        if saved.get("pending"):
            self.label("上次提交的结果尚待核对。可重试原提交；核对后再修改内容。")
            self.actions(("核对原提交", self.reconcile))
        self.apply_theme()
        return True

    def values(self):
        result = {}
        for key, widget in self.fields.items():
            if isinstance(widget, QPlainTextEdit):
                result[key] = widget.toPlainText()
            elif isinstance(widget, QCheckBox):
                result[key] = widget.isChecked()
            elif isinstance(widget, QComboBox):
                result[key] = widget.currentData()
            else:
                result[key] = widget.text()
        return result

    def _save_draft(self):
        if not self.draft_key:
            return
        # Save only into the identity captured when this form was opened. A
        # switched login must neither steal this draft nor make typing crash.
        old = self.client.local_store.draft(self.client.identity, self.form_space_id, self.draft_key) or {}
        self.client.local_store.draft(self.client.identity, self.form_space_id, self.draft_key, {**old, "values": self.values(), "extras": self.form_extras})
        self.dirty = True

    def submit(self, label, callback):
        row = self.actions((label, callback, True), ("取消", self.go_back))
        button = row.row.itemAt(0).widget()
        button.setProperty("spaceWrite", True)
        button.setProperty("spaceArchivedAllowed", label in ARCHIVED_WRITE_ACTIONS)
        self._enable_write(button)
        self.layout.addStretch()

    def reconcile(self):
        client = self.client
        saved = self.client.local_store.draft(self.client.identity, self.form_space_id, self.draft_key) or {}
        pending = saved.get("pending")
        if not pending:
            return
        def done(result):
            if result["state"] == "confirmed":
                self.client.local_store.draft(self.client.identity, self.form_space_id, self.draft_key, delete=True)
                self.dirty = False
                self.draft_key = ""
                self._invalidate_space(self.form_space_id)
                self.go_back()
                return
            self.label("中心尚未确认原操作。请重试原提交，继续使用同一操作标识，避免重复发布。")
            if pending.get("selection"):
                selection = pending["selection"]
                self.actions(("重试原发布", lambda: self.publish_files(selection["action"], selection["payload"], selection["paths"])))
            elif pending.get("action"):
                self.actions(("重试原提交", lambda: self.mutate(pending["action"], pending["payload"])))
        self._async(lambda: client.request("GET", "/team-space-operations/" + pending["id"]), done)

    def mutate(self, action, payload, *, space_id=None, done=None):
        client = self.client
        space_id = space_id if space_id is not None else self.space["id"]
        key = self.draft_key or "action:" + action + ":" + str(payload.get("id") or payload.get("member_id") or "")
        saved = self.client.local_store.draft(self.client.identity, space_id, key) or {}
        pending = saved.get("pending")
        if pending and pending["payload"] != payload:
            self.label("原提交结果尚待核对；重试会沿用原内容和操作标识。")
            self.actions(("核对并重试原操作", lambda: self.mutate(action, pending["payload"], space_id=space_id, done=done)))
            return
        pending = pending or {"id": uuid.uuid4().hex, "action": action, "payload": copy.deepcopy(payload)}
        self.client.local_store.draft(self.client.identity, space_id, key, {**saved, "pending": pending})
        def complete(result):
            client.local_store.draft(client.identity, space_id, key, delete=True)
            self.dirty, self.draft_key = False, ""
            self._invalidate_space(space_id)
            (done or (lambda _: self.open_space(space_id)))(result)
        def work():
            try:
                return client.execute(space_id, action, payload, operation_id=pending["id"])
            except TeamSpaceError as error:
                if 400 <= error.status < 500 and error.status not in {408, 429}:
                    latest = client.local_store.draft(client.identity, space_id, key) or saved
                    client.local_store.draft(client.identity, space_id, key, {**latest, "pending": None})
                raise
        self._async(work, complete)

    def _conflict(self):
        self.label("内容或权限已变化，未覆盖中心版本。请核对最新内容后再提交。")
        self.actions(("查看最新内容并保留草稿", self.compare_latest))

    def compare_latest(self):
        entity = getattr(self, "editing", None)
        if not entity:
            return
        kind, object_id = entity
        client, space_id = self.client, self.space["id"]
        def loaded(latest):
            preview = QPlainTextEdit(json.dumps(latest, ensure_ascii=False, indent=2))
            preview.setReadOnly(True)
            preview.setMinimumHeight(220)
            self.layout.insertWidget(0, preview)
            def adopt():
                self.edit_revision = latest["revision"]
                saved = self.client.local_store.draft(self.client.identity, self.form_space_id, self.draft_key) or {}
                self.client.local_store.draft(self.client.identity, self.form_space_id, self.draft_key, {**saved, "pending": None})
                self.status.setText("已采用最新版本号，草稿保留。请检查差异后再次提交。")
            self.actions(("已比较，采用最新版本号", adopt))
        self._async(lambda: client.detail(object_id) if kind == "space" else client.object(space_id, kind, object_id), loaded)

    def ask(self, message):
        return QMessageBox.question(self, "确认操作", message, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def settings(self, space):
        if space and space["role"] not in {"owner", "admin"}:
            self.settings_view()
            return
        definitions = [("name", "空间名称", "line"), ("description", "空间说明", "text")]
        values = dict(space or {})
        if space:
            definitions += [("model_mode", "公司模型范围", [("成员已获授权模型", "authorized"), ("仅选定模型", "selected")]), ("allow_personal", "允许成员使用个人模型（仅所有者可改）", "bool"), ("skills", "Skill 引用：每行 来源 | 标识 | 版本", "text")]
            values["skills"] = "\n".join(" | ".join(s[k] for k in ("source", "id", "version")) for s in space["skills"])
        if not self.form("空间设置" if space else "新建团队空间", "settings", definitions, values,
                         advanced=("model_mode", "allow_personal", "skills") if space else ()):
            return
        if space:
            self.editing, self.edit_revision = ("space", space["id"]), space["revision"]
            self.fields["allow_personal"].setEnabled(space["role"] == "owner")
            self.model_checks = self.checks([(m["name"], m["id"]) for m in space["models"]], self.form_extras.get("model_ids", space["model_ids"]))
            for box, _ in self.model_checks:
                self.layout.removeWidget(box)
                self.advanced_form.addRow(box)
                box.toggled.connect(lambda: self.save_extra("model_ids", self.checked(self.model_checks)))
            self.label("公司模型选择不增加成员授权。Skill 只保存引用，不上传密钥或个人配置。")
        def save():
            payload = self.values()
            if space:
                refs = []
                for line in payload["skills"].splitlines():
                    if not line.strip():
                        continue
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) != 3 or not all(parts):
                        raise TeamSpaceError("Skill 每行须填写来源、标识和版本。")
                    refs.append(dict(zip(("source", "id", "version"), parts)))
                payload.update(revision=self.edit_revision, skills=refs, model_ids=self.checked(self.model_checks) + [m for m in space["model_ids"] if m not in {v[1] for v in self.model_checks}])
                if space["role"] != "owner":
                    payload.pop("allow_personal")
            self.mutate("space.update" if space else "space.create", payload, space_id=space["id"] if space else "", done=lambda r: self.open_space(r["id"]))
        self.submit("保存设置" if space else "创建空间", save)
        if space and space["role"] == "owner":
            self.layout.addWidget(self.menu_button("空间管理", [("恢复空间" if space["state"] == "archived" else "归档空间", self.archive), ("转移所有权", self.transfer)]), 0, Qt.AlignLeft)

    def settings_view(self):
        client, space_id = self.client, self.space["id"]
        if self._begin_view(("settings_view", {}), ("settings_view", space_id), title="空间设置", push=True) is None:
            return
        def loaded(space):
            self.space = space
            self._spaces[space_id] = copy.deepcopy(space)
            self._clear_layout(self.layout)
            self._back_to_content("空间")
            self.label("空间设置", role="heading")
            self.label(space["description"] or "暂无说明")
            self.label("个人模型：" + ("允许" if space["allow_personal"] else "不允许"))
            self.label("空间设置由所有者和管理员维护。", role="secondary")
            self.header("")
        self._read_view(lambda: client.detail(space_id), loaded)

    def archive(self):
        archived = self.space["state"] == "archived"
        if self.ask(f"空间有 {self.space['unfinished_plans']} 条未完成计划。" + ("恢复后可以继续协作，撤回的内容不会恢复。" if archived else "归档后共享内容只读，本机任务、历史和文件保留。")):
            self.mutate("space.restore" if archived else "space.archive", {"revision": self.space["revision"]})

    def transfer(self):
        members = [(m["name"], m["id"]) for m in self.space["members"] if m["active"] and m["enabled"] and m["role"] != "owner"]
        if not members:
            raise TeamSpaceError("没有其他有效成员可接任，请先邀请成员。")
        if self.form("转移所有权", "transfer", [("member_id", "新所有者", members)]):
            self.label("转移后你成为空间管理员，新所有者可以管理个人模型开关和空间归档。")
            self.submit("转移所有权", lambda: self.mutate("space.transfer", {**self.values(), "revision": self.space["revision"]}) if self.ask("确认将所有权转交所选成员？") else None)

    def members(self):
        space_id, client = self.space["id"], self.client
        new_page = self._begin_view(("members", {}), ("members", space_id), title="空间成员", push=True)
        if new_page is None:
            return
        def loaded(space):
            self._clear_layout(self.layout)
            self.space = space
            self._spaces[space_id] = copy.deepcopy(space)
            self.header("")
            self._back_to_content("空间")
            self.label("空间成员", role="heading")
            managing = space["role"] in {"owner", "admin"}
            tree = self.table(["成员", "角色", "状态"], space["members"], lambda m: [m["name"], ROLES[m["role"]], "已退出或移除" if not m["active"] else "有效" if m["enabled"] else "账号停用"])
            def remove():
                m = self.selected(tree)
                if self.ask("移除后阻止后续访问，既有贡献保留；未完成计划需重新分配。"):
                    self.mutate("member.remove", {"member_id": m["id"], "revision": m["revision"]}, done=lambda _: self.members())
            def role():
                m = self.selected(tree)
                self.mutate("member.role", {"member_id": m["id"], "revision": m["revision"], "role": "member" if m["role"] == "admin" else "admin"}, done=lambda _: self.members())
            if managing:
                self.actions(("邀请成员", self.invite_form, True))
                options = [("移除所选成员", remove), ("查看邀请", self.invites)]
                if space["role"] == "owner":
                    options.append(("切换所选成员的管理员角色", role))
                self.layout.addWidget(self.menu_button("成员管理", options), 0, Qt.AlignLeft)
            if space["role"] != "owner":
                own = next(m for m in space["members"] if m["id"] == self.client.identity["member_id"])
                self.actions(("退出空间", lambda: self.mutate("space.leave", {"revision": own["revision"]}, done=lambda _: self.list()) if self.ask("退出后不能继续访问空间；共享贡献和本机内容保留。") else None))
            self.layout.addStretch()
        self._read_view(lambda: client.detail(space_id), loaded)

    def invite_form(self):
        if not self.form("邀请企业成员", "invite", [("search", "姓名或账号", "line")]):
            return
        def search():
            client = self.client
            query = self.values()["search"]
            def loaded(result):
                tree = self.table(["姓名", "账号"], result["items"], lambda r: [r["name"], r["account"]])
                role = self.combo([("普通成员", "member")] + ([("空间管理员", "admin")] if self.space["role"] == "owner" else []))
                self.layout.addWidget(role)
                self.actions(("发送邀请", lambda: self.mutate("invite.create", {"member_id": self.selected(tree)["id"], "role": role.currentData()}, done=lambda _: self.members()), True))
            self._async(lambda: client.request("GET", "/team-space-members", params={"search": query}), loaded)
        self.actions(("搜索成员", search, True))
        self.label("仅查询已加入企业的员工；新外部认证用户需先登录一次。邀请有效期为 7 天。")

    def invites(self, offset=0):
        client, space_id = self.client, self.space["id"]
        new_page = self._begin_view(("invites", {"offset": offset}), ("invites", space_id), title="空间邀请", push=True)
        if new_page is None:
            return
        def loaded(data):
            self._clear_layout(self.layout)
            self._back_to_content("成员")
            self.label("空间邀请", role="heading")
            tree = self.table(["成员", "角色", "状态", "有效期"], data["items"], lambda r: [r["recipient_name"], ROLES[r["role"]], STATES[r["state"]], stamp(r["expires_at"])])
            def revoke():
                r = self.selected(tree)
                self.mutate("invite.revoke", {"id": r["id"], "revision": r["revision"]}, done=lambda _: self.invites())
            self.actions(("撤销所选邀请", revoke))
            self.pager(data, offset, lambda next: self.invites(next))
        self._read_view(lambda: client.collection(space_id, "invites", offset=offset), loaded)

    def checks(self, options, selected=()):
        result = []
        for label, value in options:
            box = QCheckBox(label)
            box.setChecked(value in selected)
            self.layout.addWidget(box)
            result.append((box, value))
        return result

    @staticmethod
    def checked(checks):
        return [value for box, value in checks if box.isChecked()]

    def save_extra(self, key, value):
        self.form_extras[key] = copy.deepcopy(value)
        self._save_draft()

    def open_object(self, kind, object_id, revision_id=""):
        space_id, client = self.space["id"], self.client
        new_page = self._begin_view(("open_object", {"kind": kind, "object_id": object_id, "revision_id": revision_id}),
            ("object", space_id, kind, object_id, revision_id), push=True)
        if new_page is None:
            return
        if new_page:
            self._back_to_content("上一页")
            self._skeleton = SpaceSkeleton()
            self.layout.addWidget(self._skeleton)
        def loaded(item):
            self._clear_layout(self.layout)
            self._back_to_content("上一页")
            if kind == "snapshot":
                self.snapshot(item)
                return
            self.label(item.get("title") or item.get("name") or "留言", role="heading")
            self.label(item.get("description") or item.get("body") or "")
            if kind == "plan":
                self.label(f"{STATES[item['state']]} · 负责人：{item['assignee_name']}" + ("（已不可用）" if item["assignee_id"] and not item["assignee_available"] else ""))
                if item["completion"]:
                    self.label("完成说明：" + item["completion"])
                actions = [("在本机开始任务", lambda: self.start_form(item), True)]
                if self.can_edit(item) or item["assignee_id"] == self.client.identity["member_id"]:
                    actions.append(("更新状态", lambda: self.plan_status(item)))
                self.actions(*actions)
                more = [("查看留言", lambda: self.comments(kind, object_id))]
                if self.can_edit(item):
                    more.insert(0, ("修改计划", lambda: self.plan_form(item)))
                self.layout.addWidget(self.menu_button("更多", more), 0, Qt.AlignLeft)
                self.asset_links(item["inputs"])
                for ref in item["outcomes"]:
                    self.actions(("打开关联成果版本", lambda ref=ref: self.open_object("snapshot", ref["snapshot_id"])))
            elif kind == "asset":
                self.label("来源：" + ("已分享成果" if item["origin_share_id"] else "资料上传") + " · " + STATES[item["state"]])
                if item["origin_share_id"]:
                    self.actions(("打开来源成果", lambda: self.open_object("share", item["origin_share_id"])))
                revisions = self.combo([(f"第 {r['number']} 版 · {r['size']:,} B · {stamp(r['created_at'])}", r) for r in item["revisions"]])
                revisions.setAccessibleName("资料版本")
                if revision_id:
                    for index, revision in enumerate(item["revisions"]):
                        if revision["id"] == revision_id:
                            revisions.setCurrentIndex(index)
                    self.label("已选中原引用的固定版本。", role="secondary")
                self.layout.addWidget(revisions)
                actions = [("下载此版本", lambda: self.download(revisions.currentData(), item["name"]), True)]
                more = [("查看留言", lambda: self.comments(kind, object_id))]
                if self.can_edit(item):
                    actions.append(("上传新版本", lambda: self.upload_form(asset=item)))
                    more.extend([("整理资料", lambda: self.asset_form(item)), ("恢复资料" if item["state"] == "withdrawn" else "撤下资料", lambda: self.withdraw_asset(item))])
                self.actions(*actions)
                self.layout.addWidget(self.menu_button("更多", more), 0, Qt.AlignLeft)
                if item["references"]:
                    self.label("使用此资料的内容", role="secondary")
                    for ref in item["references"]:
                        self.layout.addWidget(self.button(ref["title"], lambda ref=ref: self.open_object(ref["kind"], ref["id"]), kind="ghost"), 0, Qt.AlignLeft)
            elif kind == "share":
                self.label("发布人：" + item["author_name"] + " · " + STATES[item["state"]])
                if item["state"] == "active":
                    versions = self.combo([(f"第 {r['number']} 个快照 · {STATES[r['source_state']]} · {stamp(r['created_at'])}", r["id"]) for r in item["snapshots"]])
                    versions.setAccessibleName("成果版本")
                    versions.activated.connect(lambda: self.open_object("snapshot", versions.currentData()))
                    self.layout.addWidget(versions)
                    if item["snapshots"]:
                        self.snapshot(item["snapshots"][0], heading=False)
                    more = [("查看留言", lambda: self.comments(kind, object_id))]
                    if item["author_id"] == self.client.identity["member_id"]:
                        more.append(("发布新快照", lambda: self.share_form(share=item)))
                    if self.can_edit(item):
                        more.append(("撤回成果", lambda: self.withdraw_share(item)))
                    self.layout.addWidget(self.menu_button("更多", more), 0, Qt.AlignLeft)
            elif kind == "comment":
                self.label(item["author_name"] + " · " + STATES[item["state"]] + (" · 已修改" if item["revision"] > 1 else ""))
                if item["state"] == "active":
                    if item["author_id"] == self.client.identity["member_id"]:
                        self.actions(("修改留言", lambda: self.comment_form(item["target_kind"], item["target_id"], item)))
                    if self.can_edit(item):
                        self.actions(("撤回留言", lambda: self.withdraw_comment(item)))
            self.layout.addStretch()
        self._read_view(lambda: client.object(space_id, kind, object_id), loaded)

    def can_edit(self, item):
        return self.space["role"] in {"owner", "admin"} or item.get("author_id") == self.client.identity["member_id"]

    def asset_links(self, refs):
        for ref in refs:
            self.actions(("查看引用资料 · " + ref["revision_id"][:8], lambda ref=ref: self.open_object("asset", ref["asset_id"], ref["revision_id"])))

    def snapshot(self, snapshot, *, heading=True):
        if heading:
            self.label(f"成果快照 · 第 {snapshot['number']} 版", role="heading")
            self.label(STATES[snapshot["source_state"]], role="secondary")
        text = QPlainTextEdit(snapshot["body"])
        text.setReadOnly(True)
        text.setMinimumHeight(260)
        self.layout.addWidget(text)
        self.asset_links(snapshot["assets"])
        self.actions(("基于此成果开始任务", lambda: self.start_form(snapshot=snapshot), True))

    def comments(self, kind, object_id, offset=0):
        space_id, client = self.space["id"], self.client
        new_page = self._begin_view(("comments", {"kind": kind, "object_id": object_id, "offset": offset}),
            ("comments", space_id, kind, object_id), title="讨论", push=True)
        if new_page is None:
            return
        if new_page:
            self._back_to_content("上一页")
            self.actions(("发布留言", lambda: self.comment_form(kind, object_id), True))
            self._build_list_area(["留言", "作者", "状态"], lambda r: self.open_object("comment", r["id"]))
        def loaded(data):
            self._fill_list(data, lambda r: [r["body"][:140] or "内容已撤回", r["author_name"], STATES[r["state"]] + (" · 已修改" if r["revision"] > 1 else ""),],
                ("还没有讨论", "发布留言，补充说明或反馈。"), offset, lambda next: self.comments(kind, object_id, next))
        self._read_view(lambda: client.collection(space_id, "comments", target_kind=kind, target_id=object_id, offset=offset), loaded)

    def comment_form(self, kind, object_id, item=None):
        if not self.form("修改留言" if item else "发布留言", "comment:" + (item["id"] if item else kind + object_id), [("body", "内容", "text")], item):
            return
        if item:
            self.editing, self.edit_revision = ("comment", item["id"]), item["revision"]
        self.submit("保存留言" if item else "发布留言", lambda: self.mutate("comment.update" if item else "comment.create", {**self.values(), **({"id": item["id"], "revision": self.edit_revision} if item else {"target_kind": kind, "target_id": object_id})}, done=lambda _: self.comments(kind, object_id)))

    def withdraw_comment(self, item):
        reason, ok = QInputDialog.getText(self, "撤回留言", "原因（撤回他人留言时必填）")
        if ok:
            self.mutate("comment.withdraw", {"id": item["id"], "revision": item["revision"], "reason": reason})

    def plan_form(self, plan=None):
        options = [("未分配", "")] + [(m["name"], m["id"]) for m in self.space["members"] if m["active"] and m["enabled"]]
        if not self.form("修改计划" if plan else "新建计划", "plan:" + (plan["id"] if plan else "new"), [("title", "计划标题", "line"), ("description", "任务说明", "text"), ("assignee_id", "负责人", options), ("priority", "优先级", [("普通", "normal"), ("高", "high")])], plan):
            return
        self.edit_revision = plan["revision"] if plan else None
        self.editing = ("plan", plan["id"]) if plan else None
        self.input_refs = copy.deepcopy((plan or {}).get("inputs", []))
        self.reference_picker(self.input_refs)
        self.submit("保存计划", lambda: self.mutate("plan.update" if plan else "plan.create", {**self.values(), "inputs": self.input_refs, **({"id": plan["id"], "revision": self.edit_revision} if plan else {})}, done=lambda r: self.open_object("plan", r["id"])))

    def plan_status(self, plan):
        client, space_id = self.client, self.space["id"]
        values = {"state": plan["state"], "completion": plan["completion"]}
        if not self.form("更新计划状态", "plan-status:" + plan["id"], [("state", "状态", [("待处理", "pending"), ("进行中", "in_progress"), ("已完成", "completed")]), ("completion", "完成说明（完成时必填）", "text"), ("reason", "重新打开原因", "text")], values):
            return
        self.editing, self.edit_revision = ("plan", plan["id"]), plan["revision"]
        outcomes = copy.deepcopy(plan["outcomes"])
        self.label("可关联已分享成果；勾选后固定该成果当前快照。")
        def loaded(data):
            checks = self.checks([(s["title"], s["id"]) for s in data["items"] if s["state"] == "active"], [s["share_id"] for s in outcomes])
            def save():
                selected = self.checked(checks)
                payload = {**self.values(), "id": plan["id"], "revision": self.edit_revision}
                def resolve():
                    refs = []
                    for share_id in selected:
                        share = client.object(space_id, "share", share_id)
                        refs.append({"share_id": share_id, "snapshot_id": share["snapshots"][0]["id"]})
                    return refs
                self._async(resolve, lambda refs: self.mutate("plan.status", {**payload, "outcomes": refs}, done=lambda _: self.open_object("plan", plan["id"])))
            self.submit("保存状态", save)
        self._async(lambda: self.all_choices("shares", client=client, space_id=space_id), loaded)

    def all_choices(self, collection, *, client, space_id):
        items = []
        for offset in range(0, 10000, 100):
            if self.cancel.is_set():
                raise TeamSpaceError("选择已取消，草稿保留。")
            page = client.collection(space_id, collection, offset=offset, limit=100)
            items.extend(page["items"])
            if not page["has_more"]:
                return {"items": items}
        raise TeamSpaceError("可选内容超过一万项，请先整理空间内容再选择。")

    def reference_picker(self, refs):
        refs[:] = copy.deepcopy(self.form_extras.get("inputs", refs))
        summary = self.label(f"已选择 {len(refs)} 个资料版本")
        def choose():
            client, space_id = self.client, self.space["id"]
            def loaded(data):
                tree = self.table(["资料", "最新版本"], [a for a in data["items"] if a["state"] == "active"], lambda r: [r["name"], r["latest"]])
                def select_asset():
                    item = self.selected(tree)
                    def versions(asset):
                        options = [f"第 {r['number']} 版 · {stamp(r['created_at'])}" for r in asset["revisions"]]
                        label, ok = QInputDialog.getItem(self, "选择资料版本", asset["name"], options, 0, False)
                        if ok:
                            revision = asset["revisions"][options.index(label)]
                            ref = {"asset_id": asset["id"], "revision_id": revision["id"]}
                            if ref not in refs:
                                refs.append(ref)
                            summary.setText(f"已选择 {len(refs)} 个资料版本")
                            self.save_extra("inputs", refs)
                    self._async(lambda: client.object(space_id, "asset", item["id"]), versions)
                self.actions(("选择资料及版本", select_asset), ("清空引用", lambda: (refs.clear(), summary.setText("未选择资料"), self.save_extra("inputs", refs))))
            self._async(lambda: self.all_choices("assets", client=client, space_id=space_id), loaded)
        self.actions(("添加共享资料版本", choose))

    def folders(self, offset=0):
        self.open_space(self.space["id"], "assets", offset=offset)

    def folder_form(self, parent_id=""):
        if self.form("新建文件夹", "folder:" + parent_id, [("name", "文件夹名称", "line")]):
            self.submit("创建文件夹", lambda: self.mutate("folder.create", {**self.values(), "parent_id": parent_id}, done=lambda _: self.open_space(self.space["id"], "assets", folder_id=parent_id)))

    def asset_form(self, item):
        client, space_id = self.client, self.space["id"]
        def loaded(data):
            options = [("根目录", "")] + [(r["name"], r["id"]) for r in data["items"]]
            if self.form("整理资料", "asset:" + item["id"], [("name", "名称", "line"), ("description", "说明", "text"), ("folder_id", "文件夹", options)], item):
                self.editing, self.edit_revision = ("asset", item["id"]), item["revision"]
                self.submit("保存资料信息", lambda: self.mutate("asset.update", {**self.values(), "id": item["id"], "revision": self.edit_revision}))
        self._async(lambda: self.all_choices("folders", client=client, space_id=space_id), loaded)

    def withdraw_asset(self, item):
        reason, ok = QInputDialog.getText(self, "恢复资料" if item["state"] == "withdrawn" else "撤下资料", "原因（撤下他人资料时必填；历史引用保留）")
        if ok:
            self.mutate("asset.restore" if item["state"] == "withdrawn" else "asset.withdraw", {"id": item["id"], "revision": item["revision"], "reason": reason})

    def download(self, revision, name):
        path, _ = QFileDialog.getSaveFileName(self, "下载指定版本", name)
        if path:
            client, space_id, ticket = self.client, self.space["id"], self.ticket
            self._async(lambda: client.download(space_id, revision["id"], path, cancel=self.cancel, progress=lambda size: self.bridge.progress.emit((client, ticket), f"已下载 {size:,} 字节")), lambda _: self.status.setText("文件已完整下载并校验。"), transfer=True)

    def upload_form(self, folder_id="", asset=None):
        if not self.form("上传新版本" if asset else "上传资料", "upload:" + (asset["id"] if asset else folder_id), [("path", "本机文件", "line"), ("description", "说明", "text"), ("keep_both", "遇到同名文件时保留两份", "bool")]):
            return
        self.actions(("选择文件", lambda: self.pick_file(self.fields["path"])))
        self.label(f"单文件上限 {self.space['limits']['file_bytes'] // 1024 // 1024} MiB。上传完整后才发布。")
        if asset:
            self.editing, self.edit_revision = ("asset", asset["id"]), asset["revision"]
        def save():
            values = self.values()
            payload = {"description": values["description"], "keep_both": values["keep_both"]}
            payload.update({"asset_id": asset["id"], "revision": self.edit_revision} if asset else {"folder_id": folder_id})
            self.publish_files("asset.publish", payload, [values["path"]])
        self.submit("上传并发布", save)

    def pick_file(self, widget):
        path, _ = QFileDialog.getOpenFileName(self, "选择要共享的文件")
        if path:
            widget.setText(path)

    def publish_files(self, action, payload, paths):
        space_id, key, client = self.space["id"], self.draft_key, self.client
        ticket = self.ticket
        saved = client.local_store.draft(client.identity, space_id, key) or {}
        pending = saved.get("pending")
        selection = {"action": action, "payload": payload, "paths": paths}
        if pending and pending.get("selection") != selection:
            raise TeamSpaceError("原附件和发布内容已冻结，请先核对原提交再修改。")
        pending = pending or {"id": uuid.uuid4().hex, "selection": selection, "files": []}
        def persist():
            client.local_store.draft(client.identity, space_id, key, {**saved, "pending": pending})
        persist()
        def work():
            status = client.request("GET", "/team-space-operations/" + pending["id"])
            if status["state"] == "confirmed":
                return status["result"]
            for index, path in enumerate(paths):
                if index >= len(pending["files"]):
                    self.bridge.progress.emit((client, ticket), "正在保存所选附件版本…")
                    pending["files"].append({"frozen": client.freeze_file(path, cancel=self.cancel), "id": uuid.uuid4().hex})
                    persist()
                file = pending["files"][index]
                file["upload_id"] = client.upload(space_id, file["frozen"], operation_id=file["id"], cancel=self.cancel, progress=lambda size, total: self.bridge.progress.emit((client, ticket), f"正在上传 {size:,} / {total:,} 字节"))
                persist()
            uploads = [f["upload_id"] for f in pending["files"]]
            full = {**payload, **({"upload_id": uploads[0]} if action == "asset.publish" else {"uploads": uploads})}
            try:
                return client.execute(space_id, action, full, operation_id=pending["id"])
            except TeamSpaceError as error:
                if 400 <= error.status < 500 and error.status not in {408, 429}:
                    latest = client.local_store.draft(client.identity, space_id, key) or saved
                    client.local_store.draft(client.identity, space_id, key, {**latest, "pending": None})
                raise
        def done(result):
            client.local_store.draft(client.identity, space_id, key, delete=True)
            for file in pending["files"]:
                path = Path(file["frozen"]["path"])
                path.unlink(missing_ok=True)
                path.parent.rmdir()
            self.dirty, self.draft_key = False, ""
            self._invalidate_space(space_id)
            self.open_object("asset" if action == "asset.publish" else "share", result["id"])
        self._async(work, done, transfer=True)

    def share_form(self, share=None, task_id=""):
        client, space_id = self.client, self.space["id"]
        source = self.host.team_space_share_source(task_id) if task_id else {"messages": [], "state": "manual", "title": "", "source_ref": ""}
        if not self.form("发布新快照" if share else "发布成果", "share:" + (share["id"] if share else task_id or "manual"), [("title", "成果标题", "line"), ("body", "共享说明与选定文字（可修改）", "text"), ("files", "选定附件（每行一个本机路径）", "text")], {"title": (share or {}).get("title") or source["title"]}):
            return
        self.editing, self.edit_revision = (("share", share["id"]), share["revision"]) if share else (None, None)
        self.label("仅此页显示的文字和文件会共享。未选择的对话、密钥与个人配置不会自动上传。来源：" + STATES[source["state"]])
        messages = [(f"{m['role']}：{m['content'][:160]}", m["content"]) for m in source["messages"]]
        checks = self.checks(messages)
        def add_text():
            text = "\n\n".join(self.checked(checks))
            if text:
                self.fields["body"].appendPlainText(text)
                for box, _ in checks:
                    box.setChecked(False)
        def add_files():
            paths, _ = QFileDialog.getOpenFileNames(self, "选择共享附件")
            if paths:
                self.fields["files"].appendPlainText("\n".join(paths))
        self.actions(("将勾选文字加入预览", add_text), ("添加文件", add_files))
        self.input_refs = []
        self.reference_picker(self.input_refs)
        def plans_loaded(data):
            plan = self.combo([("不关联计划", "")] + [(p["title"], p["id"]) for p in data["items"]], self.form_extras.get("plan_id", (share or {}).get("plan_id", "")))
            plan.currentIndexChanged.connect(lambda: self.save_extra("plan_id", plan.currentData()))
            self.layout.addWidget(plan)
            def save():
                values = self.values()
                payload = {"title": values["title"], "body": values["body"], "plan_id": plan.currentData(), "assets": self.input_refs, "source_state": source["state"], "source_ref": source["source_ref"]}
                saved = self.client.local_store.draft(self.client.identity, self.space["id"], self.draft_key) or {}
                frozen = (saved.get("pending") or {}).get("selection", {}).get("payload")
                if frozen:
                    payload.update(source_state=frozen["source_state"], source_ref=frozen["source_ref"])
                elif task_id:
                    payload["source_state"] = self.host.team_space_share_source(task_id)["state"]
                if share:
                    payload.update(id=share["id"], revision=self.edit_revision)
                paths = [p.strip() for p in values["files"].splitlines() if p.strip()]
                if self.ask(f"将向本空间成员发布 {len(payload['body'])} 字文字、{len(paths)} 个本机附件和 {len(self.input_refs)} 个共享资料版本。确认发布？"):
                    self.publish_files("share.publish", payload, paths)
            self.submit("发布成果", save)
        self._async(lambda: self.all_choices("plans", client=client, space_id=space_id), plans_loaded)

    def withdraw_share(self, share):
        client, space_id = self.client, self.space["id"]
        def loaded(preview):
            retained = [a["name"] for a in preview["attachments"] if a["retained"]]
            removed = [a["name"] for a in preview["attachments"] if not a["retained"]]
            self._reset("撤回成果", "撤回后正文不再开放读取，已有下载不能远程收回。")
            self.label("仍保留的共享资料：" + ("、".join(retained) or "无"))
            self.label("同时撤下的资料：" + ("、".join(removed) or "无"))
            reason = QPlainTextEdit()
            reason.setPlaceholderText("撤回原因（撤回他人成果时必填）")
            self.layout.addWidget(reason)
            self.actions(("确认撤回", lambda: self.mutate("share.withdraw", {"id": share["id"], "revision": preview["revision"], "reason": reason.toPlainText()}), True))
        self._async(lambda: client.request("GET", f"/team-spaces/{space_id}/shares/{share['id']}/withdrawal-preview"), loaded)

    def start_form(self, plan=None, snapshot=None):
        workspace = self.client.local_store.workspace(self.client.identity, self.space["id"])
        models = list(self.host.config_manager.iter_model_profiles())
        definitions = [("title", "本机任务名称", "line"), ("prompt", "这次要完成什么", "text"), ("directory", "空间专用工作目录", "line"), ("model", "本次模型", [(('公司提供 · ' if m.get('enterprise') else '我配置的 · ') + (m.get("display_name") or m.get("model_name") or m.get("name") or m["id"]), m["id"]) for m in models])]
        values = {"title": (plan or {}).get("title", ""), "prompt": (plan or {}).get("description", ""), "directory": (workspace or {}).get("workspace_dir", "")}
        if not self.form("开始本机任务", "start:" + (plan["id"] if plan else snapshot["id"] if snapshot else "new"), definitions, values):
            return
        self.label("首次使用请选择空目录或新建目录，不能与本地项目、其他空间或账号的目录重合。新任务创建独立会话，中心离线时暂停后续模型请求。")
        self.label("空间个人模型：" + ("允许使用" if self.space["allow_personal"] else "所有者尚未允许") + "；公司模型同时受员工授权和空间范围约束。")
        if snapshot:
            self.label(f"基于成果第 {snapshot['number']} 版 · {STATES[snapshot['source_state']]}。只读取已共享快照与所选文件，不继承原作者的私人对话。")
        def pick():
            path = QFileDialog.getExistingDirectory(self, "选择空目录作为空间专用目录")
            if path:
                self.fields["directory"].setText(path)
        self.actions(("选择目录", pick))
        self.input_refs = copy.deepcopy((plan or {}).get("inputs", []) + (snapshot or {}).get("assets", []))
        self.reference_picker(self.input_refs)
        skills = self.host._available_session_skills()
        referenced = self.space["skills"]
        matches = []
        missing = []
        for ref in referenced:
            match = next((s for s in skills if s.get("name") == ref["id"] and str(s.get("version") or "") == ref["version"]
                and ref["source"] in {s.get("source"), s.get("source_url"), s.get("source_kind"),
                    "local" if s.get("source_kind") == "user_extension" else None}
                and (s.get("dependency_status") or {"ok": True}).get("ok")
                and (s.get("config_status") or {"complete": True}).get("complete")), None)
            if match:
                matches.append(match["name"])
            else:
                missing.append(ref)
        self.label("本次技能（只影响新会话）：")
        checks = self.checks([(s.get("display_name") or s["name"], s["name"]) for s in skills], self.form_extras.get("skills", matches))
        for box, skill_name in checks:
            skill = next(s for s in skills if s["name"] == skill_name)
            if not (skill.get("dependency_status") or {"ok": True}).get("ok") or not (skill.get("config_status") or {"complete": True}).get("complete"):
                box.setChecked(False)
                box.setEnabled(False)
                box.setText(box.text() + "（依赖或配置未就绪，请到能力页面处理）")
            box.toggled.connect(lambda: self.save_extra("skills", self.checked(checks)))
        acknowledge = QCheckBox("下列 Skill 的来源、版本或本机配置不匹配；本次任务不使用这些引用")
        if missing:
            self.label("缺少指定版本：" + "、".join(r["id"] + " @ " + r["version"] for r in missing))
            self.layout.addWidget(acknowledge)
        def start():
            value = self.values()
            if not value["prompt"].strip() or not value["title"].strip():
                raise TeamSpaceError("请填写任务名称与任务内容。")
            profile = next((m for m in models if m["id"] == value["model"]), None)
            if not profile:
                raise TeamSpaceError("当前没有可选模型，请先在连接设置中配置或获取企业授权。")
            if missing and not acknowledge.isChecked():
                raise TeamSpaceError("请确认缺失 Skill 的处理方式，或安装指定版本后再启动。")
            refs, selected_skills = copy.deepcopy(self.input_refs), self.checked(checks)
            selected_space = copy.deepcopy(self.space)
            space_id, client = self.space["id"], self.client
            projects = [p["path"] for p in self.host.config_manager.get_projects(include_hidden=True)]
            def prepare():
                client.policy(space_id, profile)
                latest = client.detail(space_id)
                if latest["description_revision"] != selected_space["description_revision"] or latest["skills"] != selected_space["skills"]:
                    raise TeamSpaceError("空间说明或 Skill 引用已变化。请返回空间刷新后重新打开任务，草稿保留。")
                if latest["state"] != "active":
                    raise TeamSpaceError("空间已归档，请恢复后创建新任务。")
                binding = client.local_store.bind_workspace(client.identity, space_id, value["directory"], projects)
                inputs = []
                for ref in refs:
                    asset = client.object(space_id, "asset", ref["asset_id"])
                    revision = next((r for r in asset["revisions"] if r["id"] == ref["revision_id"]), None)
                    if not revision or asset["state"] != "active":
                        raise TeamSpaceError("所选资料版本已不可用，请重新选择。")
                    path = Path(binding["workspace_dir"]) / ".team-space-inputs" / revision["id"] / asset["name"]
                    if not path.resolve().is_relative_to(Path(binding["workspace_dir"]).resolve()):
                        raise TeamSpaceError("资料目录指向空间目录之外，请恢复独立目录后重试。")
                    client.download(space_id, revision["id"], path, cancel=self.cancel)
                    inputs.append({**ref, "name": asset["name"], "path": str(path), "sha256": revision["sha256"], "version": revision["number"]})
                context = {"space_name": latest["name"], "description": latest["description"], "description_revision": latest["description_revision"], "inputs": inputs, "skill_references": referenced}
                if plan:
                    current_plan = client.object(space_id, "plan", plan["id"])
                    if current_plan["revision"] != plan["revision"]:
                        raise TeamSpaceError("计划已更新，请重新打开计划核对后启动。任务草稿保留。")
                    context["plan"] = current_plan
                if snapshot:
                    context["snapshot"] = client.object(space_id, "snapshot", snapshot["id"])
                binding["task_context"] = context
                return {"binding": binding, "title": value["title"], "prompt": value["prompt"], "model_id": profile["id"], "skills": selected_skills}
            def launch(result):
                self.taskRequested.emit(result)
            self._async(prepare, launch, transfer=True)
        self.submit("创建任务并打开对话", start)
