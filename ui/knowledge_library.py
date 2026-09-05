"""Native knowledge workspace; all network operations run outside the UI thread."""

import copy
import json
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, QSize
from PySide6.QtGui import QDesktopServices, QFont, QTextDocument
import qtawesome as qta
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QFileDialog, QHeaderView, QHBoxLayout, QLabel,
                              QLineEdit, QMenu,
                              QPushButton, QSplitter, QStackedWidget, QTextBrowser, QTreeWidget,
                              QTreeWidgetItem, QVBoxLayout, QWidget)

from core.knowledge_library import KnowledgeService, KnowledgeError, response_data, rows, same_identity, segment
from core.theme import DesignTokens, bind_theme
from ui.primitives import ProductToolbar, product_button_style, product_field_style, apply_product_dialog


STATUS = {"uploading": "正在上传", "pending": "已上传 · 等待解析", "processing": "解析中",
          "finalizing": "正在建立索引", "completed": "可检索", "failed": "解析失败",
          "cancelled": "解析已取消", "unknown": "结果待核对", "rejected": "上传失败"}


class LibraryRow(QTreeWidgetItem):
    def text(self, column=0):
        return super().text(column)

    def data(self, column, role=None):
        return super().data(0, column) if role is None else super().data(column, role)


class LibraryTable(QTreeWidget):
    """A document table with optional project groups and a shared row API."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("LibraryTable")
        self.setColumnCount(4)
        self.setHeaderLabels(["名称", "类型", "更新时间", "状态"])
        self.setRootIsDecorated(False)
        self.setAlternatingRowColors(False)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setWordWrap(True)
        self.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for index, width in ((1, 90), (2, 120), (3, 105)):
            self.header().setSectionResizeMode(index, QHeaderView.Fixed)
            self.header().resizeSection(index, width)
        self._rows, self._groups = [], {}

    def clear(self):
        super().clear()
        self._rows, self._groups = [], {}

    def count(self):
        return len(self._rows)

    def item(self, index):
        return self._rows[index]

    def append(self, columns, payload, icon, group=""):
        row = LibraryRow(columns)
        row.setData(0, Qt.UserRole, payload)
        row.setIcon(0, icon)
        row.setSizeHint(0, QSize(0, 60 if "\n" in columns[0] else 48))
        row.setToolTip(0, columns[0])
        if group:
            if group not in self._groups:
                parent = LibraryRow([group, "", "", ""])
                parent.setData(0, Qt.UserRole, {"group": True})
                font = parent.font(0)
                font.setBold(True)
                parent.setFont(0, font)
                parent.setSizeHint(0, QSize(0, 42))
                parent.setFlags(Qt.ItemIsEnabled)
                self.addTopLevelItem(parent)
                self._groups[group] = parent
                parent.setExpanded(True)
            self._groups[group].addChild(row)
        else:
            self.addTopLevelItem(row)
        self._rows.append(row)
        return row


class KnowledgeReader(QTextBrowser):
    def loadResource(self, resource_type, name):
        # Render Markdown text without fetching arbitrary local/remote resources in a document.
        return None


def friendly_date(value):
    if not value:
        return "—"
    try:
        date = datetime.fromtimestamp(value) if isinstance(value, (int, float)) else datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone()
        return date.strftime("%m月%d日 %H:%M")
    except (ValueError, TypeError, OSError):
        return "—"


class LibraryJobs(QObject):
    completed = Signal(object, object, object)

    def __init__(self, parent):
        super().__init__(parent)
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="knowledge")
        self.completed.connect(self._deliver)

    def submit(self, work, done, failed):
        def execute():
            try:
                result, error = work(), None
            except Exception as exc:
                result, error = None, exc
            self.completed.emit((done, failed), result, error)
        self.pool.submit(execute)

    def _deliver(self, callbacks, result, error):
        try:
            (callbacks[1] if error else callbacks[0])(error if error else result)
        except Exception as exc:
            callbacks[1](exc)


class KnowledgePage(QDialog):
    referenceRequested = Signal(object, str)

    def __init__(self, parent=None, service=None, artifacts=None):
        super().__init__(parent)
        self.service = service or KnowledgeService()
        self.artifacts = artifacts or (lambda: [])
        self.jobs = LibraryJobs(self)
        self.epoch = 0
        self.scope = None
        self.current_kb = None
        self.current_ref = None
        self.current_file = ""
        self.page = 1
        self.folder = None
        self.view = "catalog"
        self.read_serial = 0
        self.setObjectName("KnowledgePage")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 16)
        layout.setSpacing(10)
        header = QHBoxLayout()
        heading = QLabel("资料库")
        heading.setObjectName("LibraryHeading")
        header.addWidget(heading)
        header.addStretch()
        layout.addLayout(header)

        self.login_box = QWidget()
        login_layout = QVBoxLayout(self.login_box)
        login_layout.setContentsMargins(0, 0, 0, 0)
        self.base = QLineEdit("http://localhost")
        self.base.setPlaceholderText("WeKnora 服务地址")
        self.email = QLineEdit()
        self.email.setPlaceholderText("WeKnora 邮箱")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("密码（不会保存）")
        for title, field in (("服务地址", self.base), ("邮箱", self.email), ("密码", self.password)):
            row = QHBoxLayout()
            row.addWidget(QLabel(title))
            row.addWidget(field, 1)
            login_layout.addLayout(row)
        login_actions = QHBoxLayout()
        self.login_button = QPushButton("连接并登录")
        self.login_button.setProperty("libraryPrimary", True)
        self.login_button.clicked.connect(self.login)
        self.password.returnPressed.connect(self.login)
        login_actions.addWidget(self.login_button)
        register = QPushButton("注册 / 账号管理")
        register.clicked.connect(lambda: self.open_management("/login", connected=False))
        login_actions.addWidget(register)
        login_actions.addStretch()
        login_layout.addLayout(login_actions)
        layout.addWidget(self.login_box)

        self.account_bar = QWidget()
        account_layout = QHBoxLayout(self.account_bar)
        account_layout.setContentsMargins(0, 0, 0, 0)
        self.account_label = QLabel()
        self.account_label.setTextFormat(Qt.PlainText)
        self.account_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.account_label.hide()
        self.tenants = QComboBox()
        self.tenants.setMinimumWidth(110)
        self.tenants.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.tenants.activated.connect(self.switch_tenant)
        account_layout.addWidget(self.tenants)
        logout = QPushButton("账号与设置")
        account_menu = QMenu(logout)
        account_menu.addAction("在 WeKnora 中管理", self.manage_current)
        account_menu.addSeparator()
        account_menu.addAction("退出账号", self.logout)
        logout.setMenu(account_menu)
        account_layout.addWidget(logout)
        header.addWidget(self.account_bar)

        toolbar = ProductToolbar(self)
        self.query = QLineEdit()
        self.query.setPlaceholderText("搜索资料内容")
        self.query.returnPressed.connect(self.search)
        toolbar.layout.addWidget(self.query, 1)
        self.search_scope = QComboBox()
        for label, value in (("全部", "all"), ("我的资料", "mine"), ("共享组织", "shared")):
            self.search_scope.addItem(label, value)
        toolbar.layout.addWidget(self.search_scope)
        search = QPushButton("搜索")
        search.clicked.connect(self.search)
        toolbar.layout.addWidget(search)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh)
        toolbar.layout.addWidget(refresh)
        self.search_toolbar = toolbar

        self.notice = QLabel("正在读取连接状态…")
        self.notice.setWordWrap(True)
        self.notice.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.notice.setTextFormat(Qt.PlainText)
        layout.addWidget(self.notice)
        self.splitter = QSplitter(Qt.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setObjectName("LibraryNavigation")
        self.tree.setIndentation(14)
        self.tree.setMinimumWidth(110)
        self.tree.itemClicked.connect(self.navigate)
        self.splitter.addWidget(self.tree)
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel("资料")
        self.title.setTextFormat(Qt.PlainText)
        self.title.setWordWrap(True)
        self.title.setObjectName("LibraryHeading")
        center_layout.addWidget(self.title)
        center_layout.addWidget(toolbar)
        actions = QHBoxLayout()
        self.use_button = QPushButton("和 Agent 一起用")
        self.use_button.setProperty("libraryPrimary", True)
        menu = QMenu(self)
        for text, mode in (("新建对话", "new"), ("添加到当前对话", "current"), ("添加到项目", "project")):
            menu.addAction(text, lambda mode=mode: self.use_reference(mode))
        self.use_button.setMenu(menu)
        actions.addWidget(self.use_button)
        self.upload_button = QPushButton("添加资料")
        self.upload_button.clicked.connect(self.upload_file)
        actions.addWidget(self.upload_button)
        self.management_button = QPushButton("管理")
        self.management_button.clicked.connect(self.manage_current)
        actions.addWidget(self.management_button)
        actions.addStretch()
        center_layout.addLayout(actions)
        folder_row = QHBoxLayout()
        self.folders = QComboBox()
        self.folders.addItem("全部文件夹", None)
        self.folders.activated.connect(self.change_folder)
        folder_row.addWidget(self.folders, 1)
        self.project_filter = QComboBox()
        self.project_filter.addItem("全部项目", "")
        self.project_filter.activated.connect(lambda _index: self.show_artifacts())
        self.project_filter.hide()
        folder_row.addWidget(self.project_filter)
        self.wiki_button = QPushButton("Wiki")
        self.wiki_button.clicked.connect(self.show_wiki)
        folder_row.addWidget(self.wiki_button)
        center_layout.addLayout(folder_row)
        self.items = LibraryTable()
        self.items.itemClicked.connect(self.select_item)
        center_layout.addWidget(self.items, 1)
        pager = QHBoxLayout()
        self.previous = QPushButton("上一页")
        self.previous.clicked.connect(lambda: self.turn_page(-1))
        self.next = QPushButton("下一页")
        self.next.clicked.connect(lambda: self.turn_page(1))
        self.page_label = QLabel()
        pager.addWidget(self.previous)
        pager.addWidget(self.page_label)
        pager.addWidget(self.next)
        pager.addStretch()
        center_layout.addLayout(pager)
        self.splitter.addWidget(center)
        self.reader = KnowledgeReader()
        self.reader.setOpenExternalLinks(False)
        self.reader.setReadOnly(True)
        self.reader.setPlaceholderText("选择一份资料，查看正文和来源。")
        self.reader.anchorClicked.connect(self.open_source)
        self.content_stack = QStackedWidget()
        center.setParent(None)
        self.content_stack.addWidget(center)
        reading = QWidget()
        reading_layout = QVBoxLayout(reading)
        reading_layout.setContentsMargins(24, 8, 24, 8)
        reading_actions = QHBoxLayout()
        back = QPushButton("返回列表")
        back.clicked.connect(self.back_to_list)
        reading_actions.addWidget(back)
        reading_actions.addStretch()
        self.reader_use = QPushButton("和 Agent 一起用")
        self.reader_use.setProperty("libraryPrimary", True)
        self.reader_use.setMenu(menu)
        reading_actions.addWidget(self.reader_use)
        self.reader_save = QPushButton("保存到资料库")
        self.reader_save.setProperty("libraryPrimary", True)
        self.reader_save.clicked.connect(self.upload_file)
        reading_actions.addWidget(self.reader_save)
        reader_manage = QPushButton("更多")
        reader_menu = QMenu(reader_manage)
        reader_menu.addAction("在 WeKnora 中打开", lambda: self.open_management(
            "/platform/knowledge-bases/" + segment(self.current_ref["kb_id"]) if self.current_ref else "/platform/knowledge-bases"))
        reader_manage.setMenu(reader_menu)
        reading_actions.addWidget(reader_manage)
        reading_layout.addLayout(reading_actions)
        self.reader_title = QLabel()
        self.reader_title.setWordWrap(True)
        self.reader_title.setTextFormat(Qt.PlainText)
        self.reader_title.setObjectName("LibraryDocumentTitle")
        reading_layout.addWidget(self.reader_title)
        reading_layout.addWidget(self.reader, 1)
        self.content_stack.addWidget(reading)
        self.splitter.addWidget(self.content_stack)
        self.splitter.setSizes([200, 800])
        layout.addWidget(self.splitter, 1)
        self.read_more = QPushButton("继续阅读下一页")
        self.read_more.clicked.connect(self.read_next)
        self.read_more.hide()
        reading_layout.addWidget(self.read_more)
        self.refresh_theme()
        bind_theme(self, self.refresh_theme, surface="management")
        QTimer.singleShot(0, self.refresh)

    def refresh_theme(self, _resolved=None):
        self.setStyleSheet(
            f"QDialog#KnowledgePage {{background:{DesignTokens.bg_main}; color:{DesignTokens.text_primary};}}"
            f"QDialog#KnowledgePage QLabel {{color:{DesignTokens.text_primary};}}"
            f"QDialog#KnowledgePage QTreeWidget, QDialog#KnowledgePage QTextBrowser "
            f"{{background:{DesignTokens.bg_main}; color:{DesignTokens.text_primary}; border:none;}}"
            f"QDialog#KnowledgePage QTreeWidget::item:selected, QDialog#KnowledgePage QListWidget::item:selected "
            f"{{background:{DesignTokens.bg_sidebar_selected}; color:{DesignTokens.primary};}}")
        self.setStyleSheet(self.styleSheet() +
            f"QLabel#LibraryHeading {{font-size:20px; font-weight:600; padding:6px 0;}}"
            f"QTreeWidget#LibraryNavigation {{background:{DesignTokens.bg_main};}}"
            f"QTreeWidget#LibraryNavigation::item {{padding:4px;}}"
            f"QLabel#LibraryDocumentTitle {{font-size:24px; font-weight:600; padding:20px 0;}}"
            f"QTreeWidget#LibraryTable::item {{border-bottom:1px solid {DesignTokens.separator}; padding:6px;}}"
            f"QHeaderView::section {{background:{DesignTokens.bg_main};color:{DesignTokens.text_secondary};border:none;padding:10px;}}")
        self.reader.document().setDefaultFont(QFont(self.font().family(), 12))
        for button in self.findChildren(QPushButton):
            button.setStyleSheet(product_button_style("primary" if button.property("libraryPrimary") else "secondary"))
        for field in self.findChildren(QLineEdit) + self.findChildren(QComboBox):
            field.setStyleSheet(product_field_style())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        narrow = self.width() < 780
        self.tree.setMaximumWidth(150 if narrow else 240)
        self.items.setColumnHidden(1, narrow)
        self.items.setColumnHidden(2, narrow)
        self.splitter.setSizes([140 if narrow else 210, max(320, self.width() - 240)])

    def back_to_list(self):
        self.read_serial += 1
        self.content_stack.setCurrentIndex(0)
        self.current_file = ""
        self.current_ref = (self.service.reference(self.scope, self.current_kb["id"], self.current_kb["name"])
                            if self.view in ("files", "wiki") and self.current_kb else None)
        self.use_button.setEnabled(bool(self.current_ref))
        self.upload_button.setText("添加资料")


    def error(self, error):
        self.notice.setText(f"{error}  可刷新重试，或打开 WeKnora 管理页面核对。")
        self.login_button.setEnabled(True)

    def run(self, work, done, message="正在加载…"):
        epoch = self.epoch
        self.notice.setText(message)
        self.jobs.submit(work, lambda result: done(result) if epoch == self.epoch else None,
                         lambda error: self.error(error) if epoch == self.epoch else None)

    def clear_view(self):
        self.epoch += 1
        self.read_serial += 1
        self.content_stack.setCurrentIndex(0)
        self.items.clear()
        self.reader.clear()
        self.read_more.hide()
        self.current_ref = None
        self.current_file = ""
        self.use_button.setEnabled(False)
        self.upload_button.setEnabled(False)
        self.project_filter.hide()
        self.folders.hide()
        self.wiki_button.hide()
        self.use_button.hide()
        self.upload_button.hide()
        self.management_button.hide()
        self.page_label.clear()
        self.previous.hide()
        self.next.hide()
        self.previous.setEnabled(False)
        self.next.setEnabled(False)

    def refresh(self):
        self.clear_view()
        self.tree.clear()
        self.current_kb = None
        self.scope = self.service.snapshot()
        connected = bool(self.scope)
        self.login_box.setVisible(not connected)
        self.account_bar.setVisible(connected)
        self.upload_button.setEnabled(False)
        if not connected:
            self.notice.setText("连接 WeKnora 后，可以使用自己的资料和共享知识。本地产物仍保存在本机。")
            self.tree.addTopLevelItem(self.node("本地产物", {"kind": "artifacts"}))
            return
        self.account_label.setText(self.scope["email"])
        self.account_bar.setToolTip(self.scope["email"])
        scope = copy.deepcopy(self.scope)
        def load():
            info = response_data(self.service.request(scope, "GET", "/api/v1/auth/me"))
            return info, self.service.catalog(scope) if scope.get("tenant_id") else None
        self.run(load, self.loaded)

    @staticmethod
    def node(title, data):
        item = QTreeWidgetItem([str(title)])
        item.setData(0, Qt.UserRole, data)
        item.setSizeHint(0, QSize(0, 36))
        kind = data.get("kind")
        names = {"recent": "fa5s.history", "artifacts": "fa5s.folder-open", "uploads": "fa5s.cloud-upload-alt", "kb": "fa5s.book", "organization": "fa5s.users"}
        if kind in names:
            item.setIcon(0, qta.icon(names[kind], color=DesignTokens.text_secondary))
        if kind == "group":
            item.setSizeHint(0, QSize(0, 44))

        return item

    def loaded(self, result):
        info, catalog = result
        self.tenants.blockSignals(True)
        self.tenants.clear()
        for member in info.get("memberships", []):
            self.tenants.addItem(member.get("tenant_name", str(member["tenant_id"])), str(member["tenant_id"]))
        self.tenants.setCurrentIndex(self.tenants.findData(self.scope["tenant_id"]))
        self.tenants.blockSignals(False)
        if catalog is None:
            self.notice.setText("请选择已加入的工作空间；创建工作空间请打开 WeKnora。")
            return
        membership = next((m for m in info.get("memberships", []) if str(m["tenant_id"]) == self.scope["tenant_id"]), {})
        self.tenant_role = membership.get("role")
        for title, kind in (("最近", "recent"), ("本地产物", "artifacts"), ("上传记录", "uploads")):
            self.tree.addTopLevelItem(self.node(title, {"kind": kind}))
        for title, key in (("我的资料", "mine"), ("工作空间其他资料", "others")):
            parent = self.node(title, {"kind": "group"})
            self.tree.addTopLevelItem(parent)
            for kb in catalog[key]:
                parent.addChild(self.node(kb["name"], {"kind": "kb", "kb": kb}))
            parent.setExpanded(True)
        shared = self.node("共享组织", {"kind": "group"})
        self.tree.addTopLevelItem(shared)
        for org in catalog["organizations"]:
            parent = self.node(org["name"], {"kind": "organization", "id": org["id"]})
            shared.addChild(parent)
            for kb in catalog["shared"]:
                if kb["organization_id"] == org["id"]:
                    parent.addChild(self.node(kb["name"], {"kind": "kb", "kb": kb}))
            parent.setExpanded(True)
        shared.setExpanded(True)
        self.navigate(self.tree.topLevelItem(0))

    def login(self):
        if not self.email.text().strip() or not self.password.text():
            self.notice.setText("请输入邮箱和密码。")
            return
        base, email, password = self.base.text(), self.email.text().strip(), self.password.text()
        self.login_button.setEnabled(False)
        def done(_result):
            self.password.clear()
            self.login_button.setEnabled(True)
            self.refresh()
        self.run(lambda: self.service.login(base, email, password), done, "正在登录 WeKnora…")

    def logout(self):
        self.clear_view()
        def done(_):
            self.refresh()
        def failed(error):
            self.refresh()
            self.notice.setText(f"本机登录态已清除。远端退出未确认：{error}")
        self.jobs.submit(self.service.logout, done, failed)

    def switch_tenant(self, index):
        tenant = self.tenants.itemData(index)
        if tenant == self.scope["tenant_id"]:
            return
        self.clear_view()
        self.run(lambda: self.service.switch_tenant(tenant), lambda _: self.refresh(), "正在切换工作空间…")

    def navigate(self, item, _column=0):
        data = item.data(0, Qt.UserRole) or {}
        kind = data.get("kind")
        if kind == "kb":
            self.open_kb(data["kb"])
        elif kind == "recent":
            self.clear_view()
            self.view = "recent"
            self.title.setText("最近访问")
            for ref in self.service.store.recent(self.scope):
                self.add_item(ref["title"], {"ref": ref})
            self.notice.setText("" if self.items.count() else "还没有最近访问的资料。从左侧打开一个资料库，或搜索你想了解的内容。")
        elif kind == "artifacts":
            self.clear_view()
            self.view = "artifacts"
            self.project_filter.clear()
            self.project_filter.addItem("全部项目", "")
            for project in sorted({item.get("project", "") for item in self.artifacts()}):
                self.project_filter.addItem(project, project)
            self.show_artifacts()
        elif kind == "uploads":
            self.show_uploads()
        elif kind == "organization":
            self.open_management("/platform/organizations/" + segment(data["id"]))

    def add_item(self, title, payload):
        document = payload.get("document", {})
        task = payload.get("task", {})
        path = payload.get("path", task.get("path", ""))
        extension = (document.get("file_type") or os.path.splitext(path or str(title))[1].lstrip(".")).lower()
        kind = {"md": "文档", "pdf": "PDF", "docx": "文档", "xlsx": "表格", "csv": "表格",
                "html": "网页", "pptx": "演示文稿"}.get(extension, "资料")
        if payload.get("ref", {}).get("wiki_slug"):
            kind = "Wiki"
        state = task.get("status") or document.get("parse_status", "")
        icon = qta.icon("fa5s.file-alt", color=DesignTokens.primary)
        return self.items.append([str(title), kind, friendly_date(document.get("updated_at") or task.get("updated_at")),
                                  STATUS.get(state, state)], payload, icon, payload.get("project", ""))

    def show_artifacts(self):
        self.project_filter.show()
        self.items.clear()
        self.title.setText("本地产物")
        selected = self.project_filter.currentData()
        for entry in self.artifacts():
            if not selected or entry.get("project") == selected:
                self.add_item(os.path.basename(entry["path"]), {"path": entry["path"], "project": entry.get("project") or "未分组"})
        self.notice.setText("选择产物后保存到资料库。" if self.items.count() else "还没有本地产物。")

    def open_kb(self, kb):
        self.clear_view()
        self.current_kb = kb
        for control in (self.folders, self.wiki_button, self.use_button, self.upload_button, self.management_button, self.previous, self.next):
            control.show()
        self.view, self.page, self.folder = "files", 1, None
        self.title.setText(kb["name"])
        self.current_ref = self.service.reference(self.scope, kb["id"], kb["name"])
        self.use_button.setEnabled(True)
        self.upload_button.setText("添加资料")
        self.upload_button.setEnabled(self.can_offer_upload(kb))
        self.upload_button.setToolTip("提交时由 WeKnora 验证写入权限。" if self.can_offer_upload(kb) else "当前资料库没有可用的写入权限。")
        self.folders.clear()
        self.folders.addItem("全部文件夹", None)
        scope = copy.deepcopy(self.scope)
        self.run(lambda: response_data(self.service.request(scope, "GET", f"/api/v1/knowledge-bases/{segment(kb['id'])}/knowledge/folders")), self.loaded_folders)

    def loaded_folders(self, data):
        self.folders.addItem("根目录", "")
        def visit(nodes, depth=0):
            for node in nodes:
                self.folders.addItem("  " * depth + node["name"], node["path"])
                visit(node.get("children") or [], depth + 1)
        visit(data.get("folders") or [])
        self.load_files()

    def load_files(self):
        scope, kb, page, folder = copy.deepcopy(self.scope), self.current_kb["id"], self.page, self.folder
        self.run(lambda: self.service.files(scope, kb, page, folder), self.loaded_files)

    def loaded_files(self, payload):
        self.items.clear()
        for control in (self.folders, self.wiki_button, self.use_button, self.upload_button, self.management_button, self.previous, self.next):
            control.show()
        self.upload_button.setEnabled(self.can_offer_upload(self.current_kb))
        self.current_ref = self.service.reference(self.scope, self.current_kb["id"], self.current_kb["name"])
        self.use_button.setEnabled(True)
        for item in rows(payload):
            self.add_item(item.get("title", item["id"]), {"document": item})
        total = int(payload.get("total", 0))
        self.page_label.setText(f"第 {self.page} 页 · {total} 份资料")
        self.previous.setEnabled(self.page > 1)
        self.next.setEnabled(self.page * 30 < total)
        self.notice.setText("选择资料阅读，或直接使用整个资料库。" if total else "这里还没有资料。")

    def change_folder(self, index):
        if self.current_kb:
            self.clear_view()
            self.view, self.page, self.folder = "files", 1, self.folders.itemData(index)
            self.current_ref = self.service.reference(self.scope, self.current_kb["id"], self.current_kb["name"])
            self.use_button.setEnabled(True)
            self.load_files()

    def turn_page(self, direction):
        self.clear_view()
        self.page = max(1, self.page + direction)
        self.show_wiki(reset=False) if self.view == "wiki" else self.load_files()

    def show_wiki(self, reset=True):
        if not self.current_kb:
            self.notice.setText("请先选择资料库。")
            return
        self.clear_view()
        if reset:
            self.page = 1
        self.view = "wiki"
        self.previous.show()
        self.next.show()
        scope, kb, page = copy.deepcopy(self.scope), self.current_kb["id"], self.page
        def done(payload):
            data = response_data(payload)
            for item in data.get("pages") or []:
                self.add_item(item["title"], {"ref": self.service.reference(scope, kb, item["title"], wiki_slug=item["slug"])})
            self.previous.setEnabled(page > 1)
            self.next.setEnabled(page < data.get("total_pages", 1))
            self.page_label.setText(f"Wiki · 第 {page} 页")
            self.notice.setText("Wiki 编辑和管理在 WeKnora 中完成。")
        self.run(lambda: self.service.request(scope, "GET", f"/api/v1/knowledgebase/{segment(kb)}/wiki/pages", params={"page": page, "page_size": 30}), done)

    def select_item(self, item, _column=0):
        self.read_serial += 1
        data = item.data(Qt.UserRole) or {}
        if data.get("group"):
            return
        self.content_stack.setCurrentIndex(1)
        self.reader_title.setText(item.text().split("\n")[0])
        self.reader_use.setVisible("path" not in data and "task" not in data)
        self.reader_save.setVisible("path" in data)
        self.reader_save.setEnabled(bool(self.scope))
        self.reader.clear()
        self.read_more.hide()
        self.current_ref, self.current_file = None, ""
        self.use_button.setEnabled(False)
        if "path" in data:
            self.current_file = data["path"]
            self.reader.setPlainText("这份产物保存在本机。保存到资料库后，可以在后续工作中继续使用。\n\n文件位置\n" + self.current_file)
            self.upload_button.setText("保存到资料库")
            self.upload_button.setEnabled(bool(self.scope))
            return
        if "task" in data:
            task = data["task"]
            self.reader.setPlainText(STATUS.get(task["status"], task["status"]) + "\n\n" + task.get("error", ""))
            self.run(lambda: self.service.check_upload(task), lambda result: self.reader.setPlainText(
                STATUS.get(result["status"], result["status"]) + "\n" + result.get("error", "")), "正在核对上传状态…")
            return
        ref = data.get("ref")
        if not ref:
            doc = data["document"]
            ref = self.service.reference(self.scope, doc["knowledge_base_id"], doc.get("title", doc["id"]), doc["id"])
        self.current_ref = ref
        self.use_button.setEnabled(True)
        if not ref.get("knowledge_id") and not ref.get("wiki_slug"):
            self.open_kb({"id": ref["kb_id"], "name": ref["title"]})
            return
        self.read_page = 1
        self.read_ref(ref)

    def read_ref(self, ref):
        self.read_serial += 1
        serial = self.read_serial
        scope = {**copy.deepcopy(self.scope), "refs": [ref]}
        args = {"kb_id": ref["kb_id"], "page": self.read_page}
        args.update({k: ref[k] for k in ("knowledge_id", "wiki_slug") if ref.get(k)})
        epoch_ref = copy.deepcopy(ref)
        def done(result):
            if self.current_ref != epoch_ref or serial != self.read_serial:
                return
            if "content" in result:
                text = result["content"]
                more = result.get("has_more", False)
            else:
                chunk_data = result["chunks"]
                if not isinstance(chunk_data, list):
                    raise KnowledgeError("invalid_response", "分块格式不匹配。")
                text = "\n\n".join(str(c.get("content", "")) for c in chunk_data)
                more = len(chunk_data) == result["page_size"]
            self.reader.document().setMarkdown(text, QTextDocument.MarkdownNoHTML)
            self.read_more.setVisible(more)
            self.notice.setText(f"第 {args['page']} 页")
            self.service.store.visit(ref)
        epoch = self.epoch
        self.notice.setText("正在阅读资料…")
        self.jobs.submit(lambda: self.service.tool(scope, "read", args),
                         lambda result: done(result) if epoch == self.epoch else None,
                         lambda error: self.error(error) if epoch == self.epoch and serial == self.read_serial else None)

    def read_next(self):
        if self.current_ref:
            self.read_page += 1
            self.read_ref(self.current_ref)

    def search(self):
        query, kind = self.query.text().strip(), self.search_scope.currentData()
        if not query or not self.scope:
            self.notice.setText("请先登录并输入搜索内容。")
            return
        self.clear_view()
        self.view = "search"
        self.title.setText("搜索结果")
        scope = copy.deepcopy(self.scope)
        def work():
            catalog = self.service.catalog(scope)
            groups = ("mine", "others", "shared") if kind == "all" else (kind,)
            bases = sorted({str(kb["id"]) for group in groups for kb in catalog[group]})
            if not bases:
                return []
            return response_data(self.service.request(scope, "POST", "/api/v1/knowledge-search",
                json={"query": query, "knowledge_base_ids": bases}))
        def done(results):
            if not isinstance(results, list):
                raise KnowledgeError("invalid_response", "搜索结果格式不匹配。")
            for item in results:
                doc_id = item.get("knowledge_id")
                kb_id = item.get("knowledge_base_id")
                if not doc_id or not kb_id:
                    raise KnowledgeError("invalid_response", "搜索结果缺少资料来源标识。")
                ref = self.service.reference(scope, kb_id, item.get("knowledge_title") or item.get("title") or doc_id, doc_id)
                self.add_item(ref["title"] + "\n" + str(item.get("content", ""))[:180], {"ref": ref})
            self.notice.setText(f"找到 {len(results)} 条相关片段。" if results else "没有匹配资料，可以调整关键词。")
        self.run(work, done, "正在搜索知识…")

    def use_reference(self, mode):
        if self.current_ref:
            self.referenceRequested.emit(copy.deepcopy(self.current_ref), mode)

    def upload_file(self):
        path = self.current_file
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "添加资料")
        if not path:
            return
        scope = copy.deepcopy(self.scope)
        self.run(lambda: self.service.catalog(scope), lambda catalog: self.choose_upload(path, scope, catalog), "正在读取目标资料库…")

    def choose_upload(self, path, scope, catalog):
        dialog = QDialog(self)
        apply_product_dialog(dialog, "KnowledgeUploadDialog")
        dialog.setWindowTitle("保存到资料库")
        layout = QVBoxLayout(dialog)
        filename = QLabel(os.path.basename(path))
        filename.setWordWrap(True)
        layout.addWidget(filename)
        targets = QComboBox()
        seen = set()
        for group in ("mine", "others", "shared"):
            for kb in catalog[group]:
                if kb["id"] in seen or not self.can_offer_upload(kb):
                    continue
                seen.add(kb["id"])
                targets.addItem(kb["name"], kb["id"])
        layout.addWidget(targets)
        if self.current_kb:
            index = targets.findData(self.current_kb["id"])
            if index >= 0:
                targets.setCurrentIndex(index)
        folder = QComboBox()
        folder.addItem("根目录", "")
        layout.addWidget(folder)
        note = QLabel("权限由 WeKnora 在提交时验证；文件不会自动覆盖已有资料。")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QHBoxLayout()
        submit = QPushButton("上传")
        submit.setEnabled(targets.count() > 0)
        submit.setStyleSheet(product_button_style("primary"))
        folder_generation = [0]
        def load_target_folders():
            folder_generation[0] += 1
            generation = folder_generation[0]
            folder.clear()
            folder.addItem("正在读取文件夹…", "")
            submit.setEnabled(False)
            kb_id = targets.currentData()
            if not kb_id:
                note.setText("没有可保存的资料库。请在 WeKnora 中创建资料库或申请写入权限。")
                return
            def done(payload):
                if generation != folder_generation[0]:
                    return
                folder.clear()
                folder.addItem("根目录", "")
                def visit(nodes, depth=0):
                    for node in nodes:
                        folder.addItem("  " * depth + node["name"], node["path"])
                        visit(node.get("children") or [], depth + 1)
                visit(response_data(payload).get("folders") or [])
                index = folder.findData(self.folder or "")
                if index >= 0:
                    folder.setCurrentIndex(index)
                submit.setEnabled(True)
            self.jobs.submit(lambda: self.service.request(scope, "GET", f"/api/v1/knowledge-bases/{segment(kb_id)}/knowledge/folders"),
                             done, lambda error: note.setText(str(error)) if generation == folder_generation[0] else None)
        targets.currentIndexChanged.connect(load_target_folders)
        load_target_folders()
        submit.clicked.connect(dialog.accept)
        cancel = QPushButton("取消")
        cancel.setStyleSheet(product_button_style("secondary"))
        cancel.clicked.connect(dialog.reject)
        buttons.addWidget(submit)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)
        if dialog.exec() != QDialog.Accepted:
            folder_generation[0] += 1
            return
        folder_generation[0] += 1
        kb_id, folder_path = targets.currentData(), folder.currentData()
        self.notice.setText("上传已提交，可在上传记录中查看状态。")
        # Upload callbacks are not discarded on navigation: the receipt belongs to the original identity.
        self.jobs.submit(lambda: self.service.upload(scope, path, kb_id, folder_path),
                         lambda task: self.notice.setText("上传完成：" + STATUS.get(task["status"], task["status"])) if self.scope and same_identity(scope, self.scope) else None,
                         lambda error: self.error(error) if self.scope and same_identity(scope, self.scope) else None)

    def can_offer_upload(self, kb):
        if getattr(self, "tenant_role", None) == "viewer" or kb.get("permission") == "viewer" or kb.get("source_from_agent"):
            return False
        if getattr(self, "tenant_role", None) == "contributor" and not kb.get("organization_id"):
            return kb.get("creator_id") == self.scope["user_id"]
        return True

    def show_uploads(self):
        self.clear_view()
        self.view = "uploads"
        self.title.setText("上传记录")
        for task in self.service.store.uploads():
            if same_identity(task["scope"], self.scope):
                self.add_item(os.path.basename(task["path"]) + " · " + STATUS.get(task["status"], task["status"]), {"task": task})
        self.notice.setText("选择记录核对远端状态；未确认的上传不会自动重传。")

    def manage_current(self):
        path = "/platform/knowledge-bases"
        if self.current_kb:
            path += "/" + segment(self.current_kb["id"])
        self.open_management(path)

    def open_management(self, path, connected=True):
        from core.knowledge_library import service_url
        try:
            base = self.scope["base_url"] if connected and self.scope else service_url(self.base.text())
            QDesktopServices.openUrl(QUrl(base + path))
        except KnowledgeError as error:
            self.error(error)

    def open_source(self, url):
        if url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)
