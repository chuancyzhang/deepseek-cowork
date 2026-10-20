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
import uuid
from urllib.parse import urlsplit

import qtawesome as qta
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from shiboken6 import isValid
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox,
    QDialog, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from core.team_spaces import TeamSpaceClient
from core.team_space_storage import TeamSpaceError, TeamSpacePaused, checksum
from core.theme import DesignTokens, bind_theme
from ui.primitives import ProductEmptyState, ProductMessageBox as QMessageBox, product_button_style, product_field_style, product_segmented_style


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


def stamp(value):
    return datetime.fromtimestamp(value).strftime("%m-%d %H:%M") if value else "—"


class _Bridge(QObject):
    done = Signal(object, object, object)
    progress = Signal(str)


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
        self.identity_label.setWordWrap(True)
        outer.addWidget(self.identity_label)
        self.toolbar = QWidget(self)
        top = QHBoxLayout(self.toolbar)
        top.setContentsMargins(0, 0, 0, 0)
        self.back_button = self.button("空间列表", self.open_list)
        top.addWidget(self.back_button)
        top.addWidget(self.button("打开空间链接", self.open_link))
        top.addStretch()
        self.refresh_button = self.button("刷新", self.refresh)
        top.addWidget(self.refresh_button)
        outer.addWidget(self.toolbar)
        self.status = QLabel("正在连接企业中心…")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status_row = QWidget(self)
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

    def apply_theme(self, *_):
        self.setStyleSheet(
            f"QDialog#TeamSpacesPage, QWidget#TeamSpaceContent, QWidget#TeamSpaceViewport "
            f"{{background:{DesignTokens.bg_main};color:{DesignTokens.text_primary};}} "
            f"QScrollArea#TeamSpaceScroll {{background:{DesignTokens.bg_main};border:none;}}")
        self.identity_label.setStyleSheet(f"color: {DesignTokens.text_secondary};")
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
        for cls in (QLineEdit, QPlainTextEdit, QComboBox):
            for widget in self.findChildren(cls):
                widget.setStyleSheet(product_field_style())
        for tree in self.findChildren(QTreeWidget):
            tree.setStyleSheet(f"QTreeWidget#SpaceTable {{background:{DesignTokens.bg_main}; color:{DesignTokens.text_primary}; border:1px solid {DesignTokens.border_subtle};}} QTreeWidget#SpaceTable::item {{padding:8px;}} QTreeWidget#SpaceTable::item:selected {{background:{DesignTokens.selection_bg};color:{DesignTokens.selection_text};}}")
        if self.sign_in_state is not None:
            for label in self.sign_in_state.findChildren(QLabel):
                if label.property("spaceIdentity"):
                    label.setStyleSheet(f"color:{DesignTokens.text_secondary};font-size:{DesignTokens.font_size_meta}px;")

    def button(self, label, callback, primary=False):
        button = QPushButton(label)
        button.setProperty("spaceKind", "primary" if primary else "secondary")
        button.setStyleSheet(product_button_style("primary" if primary else "secondary"))
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(lambda checked=False: self._call(callback))
        edits = {"新建计划", "发布留言", "发布留言 / 要求重新打开", "开始任务", "基于计划开始任务",
                 "基于此成果开始任务", "上传资料", "上传新版本", "新建文件夹", "发布成果", "发布新快照",
                 "修改计划", "更新状态 / 标记完成", "整理资料", "恢复资料", "邀请成员", "保存设置"}
        if getattr(self, "space", None) and self.space["state"] == "archived" and label in edits:
            button.setEnabled(False)
            button.setToolTip("空间已归档；所有者恢复空间后可继续编辑和创建任务。")
        return button

    def _call(self, callback):
        try:
            if self.busy and callback != self.cancel.set:
                return
            callback()
        except Exception as error:
            self.status.setText(str(error))

    def _progress(self, message):
        self.status.setText(message)

    def _reset(self, title="", subtitle=""):
        self.ticket += 1
        self.sign_in_state = None
        self.identity_label.show()
        self.toolbar.show()
        self.back_button.show()
        self.status_row.show()
        self.status.show()
        self.dirty, self.fields, self.draft_key = False, {}, ""
        self.editing = None
        self.form_extras = {}
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
        if title:
            self.label(title, role="heading")
        if subtitle:
            self.label(subtitle, role="secondary")

    def _show_sign_in(self, *, expired=False):
        state = self.client.enterprise_store.read()
        connection, member = state.get("connection") or {}, state.get("member") or {}
        if not connection:
            title, action = "连接企业，开始团队协作", "设置企业连接"
            description = "与同事共享计划、资料和成果，任务在各自电脑上完成。\n请先在“账号与连接”中设置企业连接并登录。"
        elif expired or member:
            title, action = "重新登录企业账号", "前往登录"
            description = "登录状态已失效，登录后即可继续访问团队空间。\n本机任务、已有内容和未提交草稿均已保留。"
        else:
            title, action = "登录后进入团队空间", "前往登录"
            description = "与同事共享计划、资料和成果，任务在各自电脑上完成。\n使用企业员工账号登录，即可创建或加入空间。"
        self._reset()
        self.space = None
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
        empty.action_button.clicked.connect(lambda: self._call(lambda: self.host.open_settings("账号与连接")))
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
        row = QHBoxLayout()
        for action in actions:
            row.addWidget(self.button(*action))
        row.addStretch()
        self.layout.addLayout(row)

    def table(self, columns, rows, values, activate=None):
        tree = QTreeWidget()
        tree.setObjectName("SpaceTable")
        tree.setColumnCount(len(columns))
        tree.setHeaderLabels(columns)
        tree.setRootIsDecorated(False)
        tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tree.setSelectionMode(QAbstractItemView.SingleSelection)
        tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for row in rows:
            item = QTreeWidgetItem([str(value) for value in values(row)])
            item.setData(0, Qt.UserRole, row)
            for index, value in enumerate(values(row)):
                item.setToolTip(index, str(value))
            tree.addTopLevelItem(item)
        tree.setMinimumHeight(min(380, max(140, 45 * (len(rows) + 1))))
        if activate:
            tree.itemActivated.connect(lambda item, col: self._call(lambda: activate(item.data(0, Qt.UserRole))))
            self.layout.addWidget(tree)
            self.actions(("打开所选", lambda: activate(self.selected(tree))))
        else:
            self.layout.addWidget(tree)
        return tree

    @staticmethod
    def selected(tree):
        item = tree.currentItem()
        if not item:
            raise TeamSpaceError("请先选择一项。")
        return item.data(0, Qt.UserRole)

    def _async(self, work, done, *, transfer=False, polling=False,
               loading_message="正在处理，请稍候…", refresh_error_label="刷新"):
        if self.busy:
            return
        self.busy = True
        self.cancel.clear()
        self.cancel_button.setVisible(transfer)
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText("刷新")
        if not polling:
            self.status.setText(loading_message)
            self.loading_indicator.show()
        ticket, client = self.ticket, self.client
        log.debug("team_space.ui_request_started ticket=%s route=%s", ticket, self.route[0])
        def execute():
            try:
                result = work()
                client.session()
                return result, None
            except Exception as error:
                return None, error
        future = self.pool.submit(execute)
        def completed(future):
            if isValid(self.bridge):
                self.bridge.done.emit((ticket, client, done, polling, refresh_error_label), *future.result())
        future.add_done_callback(completed)

    def _done(self, context, result, error):
        ticket, client, callback, polling, refresh_error_label = context
        self.busy = False
        self.refresh_button.setEnabled(True)
        self.cancel_button.hide()
        self.loading_indicator.hide()
        try:
            client.session()
        except TeamSpaceError:
            self._show_sign_in()
            return
        if ticket != self.ticket or client is not self.client:
            return
        if error:
            log.warning("team_space.ui_request_failed ticket=%s route=%s type=%s code=%s",
                        ticket, self.route[0], type(error).__name__, getattr(error, "code", ""))
            if getattr(error, "status", 0) == 401:
                self._show_sign_in(expired=True)
                return
            self.status.setText(str(error) + (" 草稿已保留。" if self.draft_key else ""))
            self.refresh_button.setText(refresh_error_label)
            if getattr(error, "status", 0) == 409 and self.draft_key:
                self._conflict()
            if polling:
                self.failures += 1
                if self.failures < 5:
                    self.timer.start(min(60000, 10000 * 2 ** self.failures))
            return
        self.failures = 0
        log.debug("team_space.ui_request_completed ticket=%s route=%s", ticket, self.route[0])
        self.status.setText("已更新 · " + stamp(datetime.now().timestamp()))
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
        old = self.space["event_version"]
        def updated(space):
            if space["event_version"] != old:
                self.status.setText("共享内容有更新。点击刷新核对；当前输入保留。")
        self._async(lambda: self.client.detail(self.space["id"]), updated, polling=True)

    def confirm_leave(self):
        if self.busy and self.cancel_button.isVisible():
            return QMessageBox.question(self, "传输仍在进行", "离开页面后传输继续，草稿保留。是否离开？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes
        if not self.dirty:
            return True
        return QMessageBox.question(self, "修改尚未提交", "草稿已保存在本机；离开不会提交到中心。是否离开？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def refresh(self):
        if self.busy:
            return
        fresh = self.client_factory()
        if fresh.identity != self.client.identity or fresh.generation != self.client.generation:
            self.client, self.space, self.route = fresh, None, ("list", {})
            self._reset()
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
        if self.confirm_leave():
            self.list()

    def list(self, search="", archived=False, offset=0):
        self.route = ("list", dict(search=search, archived=archived, offset=offset))
        self.space = None
        self._reset("已归档空间" if archived else "我的空间", "选择空间继续协作，或创建空间邀请同事。")
        self.back_button.hide()
        def loaded(result):
            data, inbox = result
            self.actions(("新建空间", lambda: self.settings(None), True))
            query = QLineEdit(search)
            query.setPlaceholderText("搜索已加入空间")
            query.setClearButtonEnabled(True)
            query.setAccessibleName("搜索已加入空间")
            query.returnPressed.connect(lambda: self._call(lambda: self.list(query.text(), archived)))
            search_row = QHBoxLayout()
            search_row.addWidget(query, 1)
            search_row.addWidget(self.button("搜索", lambda: self.list(query.text(), archived)))
            search_row.addWidget(self.button("活跃空间" if archived else "已归档", lambda: self.list(query.text(), not archived)))
            self.layout.addLayout(search_row)
            if inbox["items"]:
                self.label("待处理邀请")
                tree = self.table(["空间", "邀请人", "角色", "状态"], inbox["items"], lambda r: [r["space_name"], r["sender_name"], ROLES[r["role"]], STATES[r["state"]]])
                def respond(answer):
                    item = self.selected(tree)
                    self.mutate("invite.respond", {"id": item["id"], "revision": item["revision"], "response": answer}, space_id=item["space_id"], done=lambda _: self.list())
                self.actions(("接受邀请", lambda: respond("accept")), ("拒绝邀请", lambda: respond("decline")))
            if data["items"]:
                self.table(["空间", "本人角色", "共享更新"], data["items"], lambda r: [r["name"], ROLES[r["role"]], stamp(r["updated_at"])], lambda r: self.open_space(r["id"]))
            else:
                if search:
                    self.label("没有找到匹配的空间")
                    self.label("换个关键词，或清空搜索查看全部空间。", role="secondary")
                elif archived:
                    self.label("没有已归档空间")
                    self.label("归档后的空间会显示在这里，可切回“活跃空间”继续协作。", role="secondary")
                else:
                    self.label("还没有加入空间")
                    self.label("点击“新建空间”开始协作，或请同事邀请你。收到的邀请会显示在这里。", role="secondary")
            self.pager(data, offset, lambda next: self.list(search, archived, next))
            self.layout.addStretch()
        self._async(lambda: (self.client.list(search=search, archived=str(archived).lower(), offset=offset), self.client.request("GET", "/team-space-invites")), loaded,
                    loading_message="正在加载空间和邀请…", refresh_error_label="重试")

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
        if self.confirm_leave():
            self.open_space(parts[0], "plans" if len(parts) > 1 and parts[1] == "plans" else "activity",
                            target=parts[2] if len(parts) == 3 and parts[1] == "plans" else "")

    def open_space(self, space_id, tab="activity", offset=0, search="", assignee_id="", state="", folder_id="", target=""):
        if self.busy or not self.confirm_leave():
            return
        self.route = ("open_space", dict(space_id=space_id, tab=tab, offset=offset, search=search, assignee_id=assignee_id, state=state, folder_id=folder_id))
        self._reset()
        filters = {"offset": offset, "search": search}
        if tab == "plans":
            filters.update(assignee_id=assignee_id, state=state)
        if tab == "assets":
            filters["folder_id"] = folder_id
        collection = {"activity": "activity", "plans": "plans", "tasks": "shares", "assets": "assets"}[tab]
        def loaded(result):
            self.space, data = result
            self.header(tab)
            if target:
                self.open_object("plan", target)
                return
            if tab == "activity":
                self.actions(("发布留言", lambda: self.comment_form("space", space_id), True), ("查看空间留言", lambda: self.comments("space", space_id)))
                self.table(["动态", "成员", "时间"], data["items"], lambda r: [EVENTS.get(r["kind"], "空间更新"), r["actor_name"], stamp(r["created_at"])], self.open_event)
            elif tab == "plans":
                self.actions(("新建计划", lambda: self.plan_form(), True))
                assigned = QComboBox()
                assigned.addItem("全部负责人", "")
                for member in self.space["members"]:
                    assigned.addItem(member["name"], member["id"])
                assigned.setCurrentIndex(max(0, assigned.findData(assignee_id)))
                state_box = self.combo([(label, key) for key, label in [("", "全部状态"), ("pending", "待处理"), ("in_progress", "进行中"), ("completed", "已完成")]], state)
                self.layout.addWidget(assigned)
                self.layout.addWidget(state_box)
                self.actions(("筛选", lambda: self.open_space(space_id, tab, assignee_id=assigned.currentData(), state=state_box.currentData())), ("我负责的", lambda: self.open_space(space_id, tab, assignee_id=self.client.identity["member_id"])))
                self.table(["计划", "负责人", "状态", "优先级"], data["items"], lambda r: [r["title"], r["assignee_name"] + ("（已不可用）" if r["assignee_id"] and not r["assignee_available"] else ""), STATES[r["state"]], "高" if r["priority"] == "high" else "普通"], lambda r: self.open_object("plan", r["id"]))
            elif tab == "tasks":
                self.actions(("开始任务", self.start_form, True), ("发布成果", self.share_form))
                self.label("我在本机的任务 · 只有本人可见")
                tasks = self.host.chat_storage.list_team_space_conversations(**self.client.identity, space_id=space_id)
                tree = self.table(["本机任务", "最近修改"], tasks, lambda r: [r["title"], stamp(r["updated_at"])], lambda r: self.conversationRequested.emit(r["id"]))
                self.actions(("选择本机任务成果", lambda: self.share_form(task_id=self.selected(tree)["id"])))
                self.label("已分享成果 · 本空间成员可见；其他电脑只能读取共享内容，本机任务请在原电脑打开。")
                self.table(["成果", "发布人", "版本", "状态"], data["items"], lambda r: [r["title"], r["author_name"], r["latest"], STATES[r["state"]]], lambda r: self.open_object("share", r["id"]))
            else:
                self.actions(("上传资料", lambda: self.upload_form(folder_id=folder_id), True), ("新建文件夹", lambda: self.folder_form(folder_id)), ("全部文件夹", self.folders))
                query = QLineEdit(search)
                query.setPlaceholderText("搜索当前文件夹中的资料")
                self.layout.addWidget(query)
                self.actions(("搜索", lambda: self.open_space(space_id, tab, search=query.text(), folder_id=folder_id)), ("返回根目录", lambda: self.open_space(space_id, tab)))
                self.table(["资料", "更新人", "版本", "状态"], data["items"], lambda r: [r["name"], r["author_name"], r["latest"], STATES[r["state"]]], lambda r: self.open_object("asset", r["id"]))
            self.pager(data, offset, lambda next: self.open_space(space_id, tab, next, search, assignee_id, state, folder_id))
            self.layout.addStretch()
        self._async(lambda: (self.client.detail(space_id), self.client.collection(space_id, collection, **filters)), loaded,
                    loading_message="正在加载空间内容…")

    def header(self, tab=""):
        self.label(self.space["name"], role="heading")
        self.label(ROLES[self.space["role"]] + (" · 已归档，共享内容只读" if self.space["state"] == "archived" else " · 团队空间"))
        row = QHBoxLayout()
        for key, label in [("activity", "动态"), ("plans", "计划"), ("tasks", "任务"), ("assets", "资产")]:
            button = self.button(label, lambda key=key: self.open_space(self.space["id"], key))
            button.setCheckable(True)
            button.setProperty("spaceTab", True)
            button.setChecked(key == tab)
            button.setAccessibleName(label + ("，当前页" if key == tab else ""))
            row.addWidget(button)
        row.addStretch()
        self.layout.addLayout(row)
        self.actions(("成员", self.members), ("空间设置", lambda: self.settings(self.space)), ("复制空间链接", self.copy_link))

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

    def form(self, title, key, definitions, values=None):
        if not self.confirm_leave():
            return False
        self._reset(title)
        self.draft_key = key
        self.form_space_id = self.space["id"] if self.space else ""
        saved = self.client.local_store.draft(self.client.identity, self.form_space_id, key) or {}
        self.form_extras = copy.deepcopy(saved.get("extras", {}))
        values = {**(values or {}), **saved.get("values", {})}
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
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
            form.addRow(label, widget)
        self.layout.addLayout(form)
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
        self.actions((label, callback, True), ("返回空间" if self.space else "返回列表", lambda: self.open_space(self.space["id"]) if self.space else self.open_list()))
        self.layout.addStretch()

    def reconcile(self):
        saved = self.client.local_store.draft(self.client.identity, self.form_space_id, self.draft_key) or {}
        pending = saved.get("pending")
        if not pending:
            return
        def done(result):
            if result["state"] == "confirmed":
                self.client.local_store.draft(self.client.identity, self.form_space_id, self.draft_key, delete=True)
                self.dirty = False
                self.draft_key = ""
                self.refresh()
                return
            self.label("中心尚未确认原操作。请重试原提交，继续使用同一操作标识，避免重复发布。")
            if pending.get("selection"):
                selection = pending["selection"]
                self.actions(("重试原发布", lambda: self.publish_files(selection["action"], selection["payload"], selection["paths"])))
            elif pending.get("action"):
                self.actions(("重试原提交", lambda: self.mutate(pending["action"], pending["payload"])))
        self._async(lambda: self.client.request("GET", "/team-space-operations/" + pending["id"]), done)

    def mutate(self, action, payload, *, space_id=None, done=None):
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
            self.client.local_store.draft(self.client.identity, space_id, key, delete=True)
            self.dirty, self.draft_key = False, ""
            (done or (lambda _: self.open_space(space_id)))(result)
        def work():
            try:
                return self.client.execute(space_id, action, payload, operation_id=pending["id"])
            except TeamSpaceError as error:
                if 400 <= error.status < 500 and error.status not in {408, 429}:
                    latest = self.client.local_store.draft(self.client.identity, space_id, key) or saved
                    self.client.local_store.draft(self.client.identity, space_id, key, {**latest, "pending": None})
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
        self._async(lambda: self.client.detail(object_id) if kind == "space" else self.client.object(self.space["id"], kind, object_id), loaded)

    def ask(self, message):
        return QMessageBox.question(self, "确认操作", message, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def settings(self, space):
        if space and space["role"] not in {"owner", "admin"}:
            self._reset("空间设置", space["description"] or "暂无说明")
            self.label("个人模型：" + ("允许" if space["allow_personal"] else "不允许"))
            self.label("空间设置由所有者和管理员维护。")
            return
        definitions = [("name", "空间名称", "line"), ("description", "空间说明", "text")]
        values = dict(space or {})
        if space:
            definitions += [("model_mode", "公司模型范围", [("成员已获授权模型", "authorized"), ("仅选定模型", "selected")]), ("allow_personal", "允许成员使用个人模型（仅所有者可改）", "bool"), ("skills", "Skill 引用：每行 来源 | 标识 | 版本", "text")]
            values["skills"] = "\n".join(" | ".join(s[k] for k in ("source", "id", "version")) for s in space["skills"])
        if not self.form("空间设置" if space else "新建团队空间", "settings", definitions, values):
            return
        if space:
            self.editing, self.edit_revision = ("space", space["id"]), space["revision"]
            self.fields["allow_personal"].setEnabled(space["role"] == "owner")
            self.model_checks = self.checks([(m["name"], m["id"]) for m in space["models"]], self.form_extras.get("model_ids", space["model_ids"]))
            for box, _ in self.model_checks:
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
            self.actions(("恢复空间" if space["state"] == "archived" else "归档空间", self.archive), ("转移所有权", self.transfer))

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
        if not self.confirm_leave():
            return
        self.route = ("members", {})
        self._reset("空间成员")
        def loaded(space):
            self.space = space
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
                self.actions(("邀请成员", self.invite_form, True), ("移除所选成员", remove), ("查看邀请", self.invites))
            if space["role"] == "owner":
                self.actions(("切换管理员角色", role), ("转移所有权", self.transfer))
            else:
                own = next(m for m in space["members"] if m["id"] == self.client.identity["member_id"])
                self.actions(("退出空间", lambda: self.mutate("space.leave", {"revision": own["revision"]}, done=lambda _: self.list()) if self.ask("退出后不能继续访问空间；共享贡献和本机内容保留。") else None))
            self.layout.addStretch()
        self._async(lambda: self.client.detail(self.space["id"]), loaded)

    def invite_form(self):
        if not self.form("邀请企业成员", "invite", [("search", "姓名或账号", "line")]):
            return
        def search():
            query = self.values()["search"]
            def loaded(result):
                tree = self.table(["姓名", "账号"], result["items"], lambda r: [r["name"], r["account"]])
                role = self.combo([("普通成员", "member")] + ([("空间管理员", "admin")] if self.space["role"] == "owner" else []))
                self.layout.addWidget(role)
                self.actions(("发送邀请", lambda: self.mutate("invite.create", {"member_id": self.selected(tree)["id"], "role": role.currentData()}, done=lambda _: self.members()), True))
            self._async(lambda: self.client.request("GET", "/team-space-members", params={"search": query}), loaded)
        self.actions(("搜索成员", search, True))
        self.label("仅查询已加入企业的员工；新外部认证用户需先登录一次。邀请有效期为 7 天。")

    def invites(self, offset=0):
        self.route = ("invites", {"offset": offset})
        self._reset("空间邀请")
        def loaded(data):
            tree = self.table(["成员", "角色", "状态", "有效期"], data["items"], lambda r: [r["recipient_name"], ROLES[r["role"]], STATES[r["state"]], stamp(r["expires_at"])])
            def revoke():
                r = self.selected(tree)
                self.mutate("invite.revoke", {"id": r["id"], "revision": r["revision"]}, done=lambda _: self.invites())
            self.actions(("撤销所选邀请", revoke))
            self.pager(data, offset, lambda next: self.invites(next))
        self._async(lambda: self.client.collection(self.space["id"], "invites", offset=offset), loaded)

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
        if not self.confirm_leave():
            return
        self.route = ("open_object", {"kind": kind, "object_id": object_id, "revision_id": revision_id})
        self._reset()
        def loaded(item):
            if kind == "snapshot":
                self.snapshot(item)
                return
            self.label(item.get("title") or item.get("name") or "留言")
            self.label(item.get("description") or item.get("body") or "")
            if kind == "plan":
                self.label(f"{STATES[item['state']]} · 负责人：{item['assignee_name']}" + ("（已不可用）" if item["assignee_id"] and not item["assignee_available"] else ""))
                if item["completion"]:
                    self.label("完成说明：" + item["completion"])
                self.actions(("基于计划开始任务", lambda: self.start_form(item), True), ("查看留言", lambda: self.comments(kind, object_id)))
                if self.can_edit(item) or item["assignee_id"] == self.client.identity["member_id"]:
                    self.actions(("更新状态 / 标记完成", lambda: self.plan_status(item)))
                if self.can_edit(item):
                    self.actions(("修改计划", lambda: self.plan_form(item)))
                self.asset_links(item["inputs"])
                for ref in item["outcomes"]:
                    self.actions(("打开关联成果版本", lambda ref=ref: self.open_object("snapshot", ref["snapshot_id"])))
            elif kind == "asset":
                self.label("来源：" + ("已分享成果" if item["origin_share_id"] else "资料上传") + " · " + STATES[item["state"]])
                if item["origin_share_id"]:
                    self.actions(("打开来源成果", lambda: self.open_object("share", item["origin_share_id"])))
                revisions = self.table(["版本", "大小", "更新时间"], item["revisions"], lambda r: [r["number"], f"{r['size']:,} B", stamp(r["created_at"])])
                for index in range(revisions.topLevelItemCount()):
                    row = revisions.topLevelItem(index)
                    if row.data(0, Qt.UserRole)["id"] == revision_id or (not revision_id and index == 0):
                        revisions.setCurrentItem(row)
                        if revision_id:
                            self.label("已选中原引用的固定版本；下载时请核对版本号。")
                self.actions(("下载所选版本", lambda: self.download(self.selected(revisions), item["name"]), True), ("查看留言", lambda: self.comments(kind, object_id)))
                if self.can_edit(item):
                    self.actions(("上传新版本", lambda: self.upload_form(asset=item)), ("整理资料", lambda: self.asset_form(item)), ("恢复资料" if item["state"] == "withdrawn" else "撤下资料", lambda: self.withdraw_asset(item)))
                self.label("引用情况：" + json.dumps(item["references"], ensure_ascii=False))
            elif kind == "share":
                self.label("发布人：" + item["author_name"] + " · " + STATES[item["state"]])
                if item["state"] == "active":
                    versions = self.table(["版本", "来源状态", "发布时间"], item["snapshots"], lambda r: [r["number"], STATES[r["source_state"]], stamp(r["created_at"])], self.snapshot)
                    if versions.topLevelItemCount():
                        versions.setCurrentItem(versions.topLevelItem(0))
                    self.actions(("查看留言", lambda: self.comments(kind, object_id)))
                    if item["author_id"] == self.client.identity["member_id"]:
                        self.actions(("发布新快照", lambda: self.share_form(share=item)))
                    if self.can_edit(item):
                        self.actions(("撤回成果", lambda: self.withdraw_share(item)))
            elif kind == "comment":
                self.label(item["author_name"] + " · " + STATES[item["state"]] + (" · 已修改" if item["revision"] > 1 else ""))
                if item["state"] == "active":
                    if item["author_id"] == self.client.identity["member_id"]:
                        self.actions(("修改留言", lambda: self.comment_form(item["target_kind"], item["target_id"], item)))
                    if self.can_edit(item):
                        self.actions(("撤回留言", lambda: self.withdraw_comment(item)))
            self.layout.addStretch()
        self._async(lambda: self.client.object(self.space["id"], kind, object_id), loaded)

    def can_edit(self, item):
        return self.space["role"] in {"owner", "admin"} or item.get("author_id") == self.client.identity["member_id"]

    def asset_links(self, refs):
        for ref in refs:
            self.actions(("查看引用资料 · " + ref["revision_id"][:8], lambda ref=ref: self.open_object("asset", ref["asset_id"], ref["revision_id"])))

    def snapshot(self, snapshot):
        self._reset(f"成果快照 · 第 {snapshot['number']} 版", STATES[snapshot["source_state"]])
        text = QPlainTextEdit(snapshot["body"])
        text.setReadOnly(True)
        text.setMinimumHeight(260)
        self.layout.addWidget(text)
        self.asset_links(snapshot["assets"])
        self.actions(("基于此成果开始任务", lambda: self.start_form(snapshot=snapshot), True))

    def comments(self, kind, object_id, offset=0):
        self.route = ("comments", {"kind": kind, "object_id": object_id, "offset": offset})
        self._reset("留言")
        def loaded(data):
            self.actions(("发布留言 / 要求重新打开", lambda: self.comment_form(kind, object_id), True))
            self.table(["留言", "作者", "状态"], data["items"], lambda r: [r["body"][:140] or "内容已撤回", r["author_name"], STATES[r["state"]] + (" · 已修改" if r["revision"] > 1 else "")], lambda r: self.open_object("comment", r["id"]))
            self.pager(data, offset, lambda next: self.comments(kind, object_id, next))
        self._async(lambda: self.client.collection(self.space["id"], "comments", target_kind=kind, target_id=object_id, offset=offset), loaded)

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
                        share = self.client.object(self.space["id"], "share", share_id)
                        refs.append({"share_id": share_id, "snapshot_id": share["snapshots"][0]["id"]})
                    return refs
                self._async(resolve, lambda refs: self.mutate("plan.status", {**payload, "outcomes": refs}, done=lambda _: self.open_object("plan", plan["id"])))
            self.submit("保存状态", save)
        self._async(lambda: self.all_choices("shares"), loaded)

    def all_choices(self, collection):
        items = []
        for offset in range(0, 10000, 100):
            if self.cancel.is_set():
                raise TeamSpaceError("选择已取消，草稿保留。")
            page = self.client.collection(self.space["id"], collection, offset=offset, limit=100)
            items.extend(page["items"])
            if not page["has_more"]:
                return {"items": items}
        raise TeamSpaceError("可选内容超过一万项，请先整理空间内容再选择。")

    def reference_picker(self, refs):
        refs[:] = copy.deepcopy(self.form_extras.get("inputs", refs))
        summary = self.label(f"已选择 {len(refs)} 个资料版本")
        def choose():
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
                    self._async(lambda: self.client.object(self.space["id"], "asset", item["id"]), versions)
                self.actions(("选择资料及版本", select_asset), ("清空引用", lambda: (refs.clear(), summary.setText("未选择资料"), self.save_extra("inputs", refs))))
            self._async(lambda: self.all_choices("assets"), loaded)
        self.actions(("添加共享资料版本", choose))

    def folders(self, offset=0):
        self.route = ("folders", {"offset": offset})
        self._reset("文件夹")
        def loaded(data):
            self.table(["文件夹", "上级"], data["items"], lambda r: [r["name"], r["parent_id"] or "根目录"], lambda r: self.open_space(self.space["id"], "assets", folder_id=r["id"]))
            self.pager(data, offset, lambda next: self.folders(next))
        self._async(lambda: self.client.collection(self.space["id"], "folders", offset=offset), loaded)

    def folder_form(self, parent_id=""):
        if self.form("新建文件夹", "folder:" + parent_id, [("name", "文件夹名称", "line")]):
            self.submit("创建文件夹", lambda: self.mutate("folder.create", {**self.values(), "parent_id": parent_id}, done=lambda _: self.open_space(self.space["id"], "assets", folder_id=parent_id)))

    def asset_form(self, item):
        def loaded(data):
            options = [("根目录", "")] + [(r["name"], r["id"]) for r in data["items"]]
            if self.form("整理资料", "asset:" + item["id"], [("name", "名称", "line"), ("description", "说明", "text"), ("folder_id", "文件夹", options)], item):
                self.editing, self.edit_revision = ("asset", item["id"]), item["revision"]
                self.submit("保存资料信息", lambda: self.mutate("asset.update", {**self.values(), "id": item["id"], "revision": self.edit_revision}))
        self._async(lambda: self.all_choices("folders"), loaded)

    def withdraw_asset(self, item):
        reason, ok = QInputDialog.getText(self, "恢复资料" if item["state"] == "withdrawn" else "撤下资料", "原因（撤下他人资料时必填；历史引用保留）")
        if ok:
            self.mutate("asset.restore" if item["state"] == "withdrawn" else "asset.withdraw", {"id": item["id"], "revision": item["revision"], "reason": reason})

    def download(self, revision, name):
        path, _ = QFileDialog.getSaveFileName(self, "下载指定版本", name)
        if path:
            self._async(lambda: self.client.download(self.space["id"], revision["id"], path, cancel=self.cancel, progress=lambda size: self.bridge.progress.emit(f"已下载 {size:,} 字节")), lambda _: self.status.setText("文件已完整下载并校验。"), transfer=True)

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
                    self.bridge.progress.emit("正在保存所选附件版本…")
                    pending["files"].append({"frozen": client.freeze_file(path, cancel=self.cancel), "id": uuid.uuid4().hex})
                    persist()
                file = pending["files"][index]
                file["upload_id"] = client.upload(space_id, file["frozen"], operation_id=file["id"], cancel=self.cancel, progress=lambda size, total: self.bridge.progress.emit(f"正在上传 {size:,} / {total:,} 字节"))
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
            self.open_object("asset" if action == "asset.publish" else "share", result["id"])
        self._async(work, done, transfer=True)

    def share_form(self, share=None, task_id=""):
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
        self._async(lambda: self.all_choices("plans"), plans_loaded)

    def withdraw_share(self, share):
        def loaded(preview):
            retained = [a["name"] for a in preview["attachments"] if a["retained"]]
            removed = [a["name"] for a in preview["attachments"] if not a["retained"]]
            self._reset("撤回成果", "撤回后正文不再开放读取，已有下载不能远程收回。")
            self.label("仍保留的共享资产：" + ("、".join(retained) or "无"))
            self.label("同时撤下的资产：" + ("、".join(removed) or "无"))
            reason = QPlainTextEdit()
            reason.setPlaceholderText("撤回原因（撤回他人成果时必填）")
            self.layout.addWidget(reason)
            self.actions(("确认撤回", lambda: self.mutate("share.withdraw", {"id": share["id"], "revision": preview["revision"], "reason": reason.toPlainText()}), True))
        self._async(lambda: self.client.request("GET", f"/team-spaces/{self.space['id']}/shares/{share['id']}/withdrawal-preview"), loaded)

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
