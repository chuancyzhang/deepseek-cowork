"""Native knowledge workspace; all network operations run outside the UI thread."""

import copy
import json
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, QSize, QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QDesktopServices, QFont, QTextDocument, QPixmap, QIcon
import qtawesome as qta
from PySide6.QtWidgets import (QStyledItemDelegate, QStyleOptionViewItem, QStyle, QAbstractItemView, QComboBox, QDialog, QFileDialog, QHeaderView, QHBoxLayout, QLabel,
                              QLineEdit, QListWidget, QListWidgetItem, QMenu,
                              QPushButton, QSplitter, QStackedWidget, QTextBrowser, QTreeWidget,
                              QTreeWidgetItem, QVBoxLayout, QWidget)

from core.knowledge_library import KnowledgeService, KnowledgeError, response_data, rows, same_identity, segment
from core.theme import DesignTokens, bind_theme
from ui.primitives import ProductPopover, ProductToolbar, product_button_style, product_field_style, apply_product_dialog


WIKI_TYPES = {"index": "概览与导航", "concept": "概念", "entity": "人物与事物", "synthesis": "专题综述", "comparison": "对比分析", "summary": "文档摘要"}


STATUS = {"uploading": "正在上传", "pending": "已上传 · 等待解析", "processing": "解析中",
          "finalizing": "正在建立索引", "completed": "可检索", "failed": "解析失败",
          "cancelled": "解析已取消", "unknown": "结果待核对", "rejected": "上传失败"}


class LibraryRow(QTreeWidgetItem):
    def text(self, column=0):
        return super().text(column)

    def data(self, column, role=None):
        return super().data(0, column) if role is None else super().data(column, role)


class LibrarySelectionDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if index.column() == 0 and index.data(Qt.CheckStateRole) is not None:
            visible = bool(option.state & QStyle.State_MouseOver) or index.data(Qt.CheckStateRole) == Qt.Checked.value
            if visible:
                option.icon = QIcon()
            else:
                option.features &= ~QStyleOptionViewItem.HasCheckIndicator


class LibraryTable(QTreeWidget):
    """A document table with optional project groups and a shared row API."""
    fileActivated = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setItemDelegate(LibrarySelectionDelegate(self))
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

    def checkbox_rect(self, item):
        index = self.indexFromItem(item, 0)
        option = QStyleOptionViewItem()
        option.initFrom(self)
        option.rect = self.visualRect(index)
        option.state |= QStyle.State_MouseOver
        self.itemDelegate().initStyleOption(option, index)
        return self.style().subElementRect(QStyle.SE_ItemViewItemCheckIndicator, option, self)

    def mousePressEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        self._checkbox_pressed = None
        if event.button() == Qt.LeftButton and item and item.data(0, Qt.CheckStateRole) is not None and self.checkbox_rect(item).contains(event.position().toPoint()):
            self._checkbox_pressed = item
            self.setCurrentItem(item)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        checkbox = bool(item and item.flags() & Qt.ItemIsUserCheckable and self.checkbox_rect(item).contains(event.position().toPoint()))
        pressed = getattr(self, "_checkbox_pressed", None)
        self._checkbox_pressed = None
        if pressed is not None:
            if item is pressed and checkbox and event.button() == Qt.LeftButton:
                item.setCheckState(0, Qt.Unchecked if item.checkState(0) == Qt.Checked else Qt.Checked)
            event.accept()
            return
        super().mouseReleaseEvent(event)
        if item and event.button() == Qt.LeftButton and not checkbox:
            self.fileActivated.emit(item)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.currentItem():
            self.fileActivated.emit(self.currentItem())
            return
        super().keyPressEvent(event)

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
        if any(key in payload for key in ("ref", "document")):
            row.setFlags(row.flags() | Qt.ItemIsUserCheckable)
            row.setCheckState(0, Qt.Unchecked)
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


class KnowledgeReferencePopover(ProductPopover):
    applied = Signal(object)

    def __init__(self, references, parent=None):
        super().__init__(parent, width=500)
        self.references = copy.deepcopy(references)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        layout.addWidget(QLabel("本对话使用的资料"))
        subtitle = QLabel("取消勾选可移除资料；应用后从下一次提交生效。")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索已选资料")
        self.search.setStyleSheet(product_field_style())
        layout.addWidget(self.search)
        self.items = QListWidget()
        self.items.setMinimumHeight(160)
        self.items.setMaximumHeight(280)
        self.items.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.items.setWordWrap(True)
        self.items.setStyleSheet(f"QListWidget {{background:{DesignTokens.bg_main};color:{DesignTokens.text_primary};border:1px solid {DesignTokens.separator};border-radius:7px;padding:4px;}} QListWidget::item {{padding:8px;}}")
        for ref in self.references:
            item = QListWidgetItem(ref.get("title") or "资料")
            item.setToolTip(item.text())
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self.items.addItem(item)
        self.search.textChanged.connect(self.filter_items)
        layout.addWidget(self.items)
        actions = QHBoxLayout()
        clear = QPushButton("清除")
        clear.clicked.connect(self.clear_selection)
        clear.setStyleSheet(product_button_style("ghost"))
        actions.addWidget(clear)
        actions.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.close)
        cancel.setStyleSheet(product_button_style("secondary"))
        actions.addWidget(cancel)
        self.apply_button = QPushButton("应用")
        self.apply_button.setStyleSheet(product_button_style("primary"))
        self.apply_button.clicked.connect(self.apply_selection)
        actions.addWidget(self.apply_button)
        layout.addLayout(actions)

    def filter_items(self, text):
        for index in range(self.items.count()):
            item = self.items.item(index)
            item.setHidden(text.casefold() not in item.text().casefold())

    def clear_selection(self):
        for index in range(self.items.count()):
            self.items.item(index).setCheckState(Qt.Unchecked)

    def apply_selection(self):
        self.applied.emit([ref for index, ref in enumerate(self.references)
                           if self.items.item(index).checkState() == Qt.Checked])
        self.close()


class KnowledgeProjectPicker(QDialog):
    """Short, searchable project selection using the product dialog theme."""
    def __init__(self, projects, parent=None):
        super().__init__(parent)
        apply_product_dialog(self, "KnowledgeProjectPicker")
        self.setWindowTitle("添加资料到项目")
        self.resize(420, 400)
        self.selected_project = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("选择项目，供之后的新对话使用这份资料。"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索项目")
        self.search.setClearButtonEnabled(True)
        self.search.setStyleSheet(product_field_style())
        layout.addWidget(self.search)
        self.projects = QListWidget()
        self.projects.setEditTriggers(QAbstractItemView.NoEditTriggers)
        for project in projects:
            if project.get("archived") or project.get("hidden"):
                continue
            item = QListWidgetItem(str(project["name"]))
            item.setData(Qt.UserRole, copy.deepcopy(project))
            item.setToolTip(project["path"])
            item.setSizeHint(QSize(0, 38))
            self.projects.addItem(item)
        layout.addWidget(self.projects, 1)
        self.empty = QLabel("没有匹配的项目")
        layout.addWidget(self.empty)
        self.search.textChanged.connect(self.filter_projects)
        actions = QHBoxLayout()
        actions.addStretch()
        cancel = QPushButton("取消")
        cancel.setStyleSheet(product_button_style("secondary"))
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        self.submit = QPushButton("添加到项目")
        self.submit.setStyleSheet(product_button_style("primary"))
        self.submit.clicked.connect(self.choose)
        actions.addWidget(self.submit)
        layout.addLayout(actions)
        self.projects.itemSelectionChanged.connect(self.update_selection)
        self.projects.itemDoubleClicked.connect(lambda _item: self.choose())
        self.filter_projects("")
        self.search.setFocus()

    def filter_projects(self, query):
        visible = 0
        for index in range(self.projects.count()):
            item = self.projects.item(index)
            hidden = query.casefold() not in item.text().casefold()
            item.setHidden(hidden)
            visible += not hidden
        self.empty.setVisible(not visible)
        self.update_selection()

    def update_selection(self):
        item = self.projects.currentItem()
        self.submit.setEnabled(bool(item and not item.isHidden()))

    def choose(self):
        item = self.projects.currentItem()
        if item and not item.isHidden():
            self.selected_project = copy.deepcopy(item.data(Qt.UserRole))
            self.accept()


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
        self.wiki_filter = QComboBox()
        self.wiki_filter.addItem("全部分类", "")
        for key, label in WIKI_TYPES.items():
            self.wiki_filter.addItem(label, key)
        self.wiki_filter.activated.connect(lambda _index: self.show_wiki())
        folder_row.addWidget(self.wiki_filter, 1)
        self.files_button = QPushButton("返回资料")
        self.files_button.setMaximumWidth(120)
        self.files_button.clicked.connect(lambda: self.open_kb(self.current_kb))
        folder_row.addWidget(self.files_button)
        center_layout.addLayout(folder_row)
        self.items = LibraryTable()
        self.items.fileActivated.connect(self.select_item)
        self.items.itemChanged.connect(lambda *_args: self.update_batch_selection())
        center_layout.addWidget(self.items, 1)
        self.batch_bar = QWidget()
        self.batch_bar.setObjectName("KnowledgeBatchBar")
        selection_actions = QHBoxLayout(self.batch_bar)
        selection_actions.setContentsMargins(8, 6, 8, 6)
        self.batch_count = QLabel()
        selection_actions.addWidget(self.batch_count)
        clear_selection = QPushButton("取消选择")
        clear_selection.clicked.connect(lambda: self.check_page(False))
        selection_actions.addWidget(clear_selection)
        selection_actions.addStretch()
        batch_add = QPushButton("添加到…")
        batch_add.setProperty("libraryPrimary", True)
        batch_menu = QMenu(batch_add)
        for label, mode in (("新建对话", "new"), ("当前对话", "current"), ("项目", "project")):
            batch_menu.addAction(label, lambda checked=False, mode=mode: self.use_reference(mode))
        batch_add.setMenu(batch_menu)
        selection_actions.addWidget(batch_add)
        center_layout.addWidget(self.batch_bar)
        self.batch_bar.hide()
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
        reader_menu.addAction("用本机应用打开", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.current_file)) if self.current_file else None)
        reader_manage.setMenu(reader_menu)
        reading_actions.addWidget(reader_manage)
        reading_layout.addLayout(reading_actions)
        self.reader_title = QLabel()
        self.reader_title.setWordWrap(True)
        self.reader_title.setTextFormat(Qt.PlainText)
        self.reader_title.setObjectName("LibraryDocumentTitle")
        reading_layout.addWidget(self.reader_title)
        self.reader_stack = QStackedWidget()
        self.reader_stack.addWidget(self.reader)
        reading_layout.addWidget(self.reader_stack, 1)
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
        self.upload_poll_busy = False
        self.upload_timer = QTimer(self)
        self.upload_timer.setInterval(5000)
        self.upload_timer.timeout.connect(self.poll_uploads)
        self.upload_timer.start()
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
        self.release_preview()
        self.epoch += 1
        self.read_serial += 1
        self.content_stack.setCurrentIndex(0)
        self.items.clear()
        self.batch_bar.hide()
        self.reader_stack.setCurrentIndex(0)
        self.reader.clear()
        self.read_more.hide()
        self.current_ref = None
        self.current_file = ""
        self.use_button.setEnabled(False)
        self.upload_button.setEnabled(False)
        self.project_filter.hide()
        self.folders.hide()
        self.wiki_button.hide()
        self.wiki_filter.hide()
        self.files_button.hide()
        self.items.setHeaderLabels(["名称", "类型", "更新时间", "状态"])
        self.items.setColumnHidden(3, False)
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
                self.add_item(ref["title"], {"ref": ref, "group_name": "Wiki 阅读" if ref.get("wiki_slug") else "资料阅读"})
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
        document = payload.get("document", payload.get("wiki", {}))
        task = payload.get("task", {})
        path = payload.get("path", task.get("path", ""))
        extension = (document.get("file_type") or os.path.splitext(path or str(title))[1].lstrip(".")).lower()
        kind = {"md": "文档", "pdf": "PDF", "docx": "文档", "xlsx": "表格", "csv": "表格",
                "html": "网页", "pptx": "演示文稿"}.get(extension, "资料")
        if payload.get("ref", {}).get("wiki_slug"):
            kind = WIKI_TYPES.get(document.get("page_type"), "Wiki")
        state = task.get("status") or document.get("parse_status", "")
        icon = qta.icon("fa5s.file-alt", color=DesignTokens.primary)
        return self.items.append([str(title), kind, friendly_date(document.get("updated_at") or task.get("updated_at")),
                                  STATUS.get(state, state)], payload, icon, payload.get("group_name", payload.get("project", "")))

    def show_artifacts(self):
        self.project_filter.show()
        self.items.clear()
        self.title.setText("本地产物")
        selected = self.project_filter.currentData()
        for entry in self.artifacts():
            if not selected or entry.get("project") == selected:
                self.add_item(os.path.basename(entry["path"]), {"path": entry["path"], "project": entry.get("project") or "未分组"})
        self.update_batch_selection()
        self.notice.setText("点击产物打开预览。" if self.items.count() else "还没有本地产物。")

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
        self.notice.setText("点击资料打开阅读，悬停勾选可批量加入对话或项目。" if total else "这里还没有资料。")

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
        self.title.setText(self.current_kb["name"] + " · Wiki")
        self.wiki_filter.show()
        self.files_button.show()
        self.management_button.show()
        self.items.setHeaderLabels(["标题", "分类", "更新时间", ""])
        self.items.setColumnHidden(3, True)
        self.previous.show()
        self.next.show()
        scope, kb, page = copy.deepcopy(self.scope), self.current_kb["id"], self.page
        page_type_filter = self.wiki_filter.currentData()
        def done(payload):
            data = response_data(payload)
            pages = data.get("pages") or []
            order = {key: index for index, key in enumerate(WIKI_TYPES)}
            for item in sorted(pages, key=lambda item: (order.get(item.get("page_type"), 99), item["title"].casefold())):
                page_type = item.get("page_type")
                category = " / ".join(item.get("category_path") or [])
                group = WIKI_TYPES.get(page_type, "其他页面")
                if category:
                    group += " · " + category
                self.add_item(item["title"], {"wiki": item, "group_name": group,
                    "ref": self.service.reference(scope, kb, item["title"], wiki_slug=item["slug"])})
            self.previous.setEnabled(page > 1)
            self.next.setEnabled(page < data.get("total_pages", 1))
            self.page_label.setText(f"Wiki · 第 {page} 页")
            self.notice.setText("按分类浏览 Wiki。Wiki 编辑和目录管理在 WeKnora 中完成。" if pages else "此分类暂无 Wiki 页面。Wiki 编辑和管理在 WeKnora 中完成。")
        self.run(lambda: self.service.request(scope, "GET", f"/api/v1/knowledgebase/{segment(kb)}/wiki/pages", params={"page": page, "page_size": 30, "page_type": page_type_filter, "sort_by": "title", "sort_order": "asc"}), done)

    def select_item(self, item, _column=0):
        self.release_preview()
        self.read_serial += 1
        data = item.data(Qt.UserRole) or {}
        if data.get("group"):
            return
        self.content_stack.setCurrentIndex(1)
        self.reader_title.setText(item.text().split("\n")[0])
        self.reader_use.setVisible("path" not in data and "task" not in data)
        self.reader_save.setVisible("path" in data)
        self.reader_save.setEnabled(bool(self.scope))
        self.reader_stack.setCurrentIndex(0)
        self.reader.clear()
        self.read_more.hide()
        self.current_ref, self.current_file = None, ""
        self.use_button.setEnabled(False)
        if "path" in data:
            self.current_file = data["path"]
            self.preview_local_file(self.current_file)
            self.upload_button.setText("保存到资料库")
            self.upload_button.setEnabled(bool(self.scope))
            return
        if "task" in data:
            task = data["task"]
            self.current_file = task["path"]
            if os.path.isfile(self.current_file):
                self.preview_local_file(self.current_file)
            else:
                self.reader.setPlainText("本机源文件已移动或删除。上传记录仍可核对。")
            self.run(lambda: self.service.check_upload(task), lambda result: self.notice.setText(
                STATUS.get(result["status"], result["status"]) + " " + result.get("error", "")), "正在核对上传状态…")
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
        if ref["title"].lower().endswith((".html", ".htm")) and ref.get("knowledge_id"):
            scope = copy.deepcopy(self.scope)
            self.run(lambda: self.service.preview_html(scope, ref["knowledge_id"]),
                     lambda content: self.show_html(content) if self.current_ref == ref else None, "正在读取原文件预览…")
            return
        self.read_page = 1
        self.read_ref(ref)

    def release_preview(self):
        if hasattr(self, "pdf_document"):
            self.pdf_document.close()
            self.pdf_buffer.close()

    def closeEvent(self, event):
        self.release_preview()
        super().closeEvent(event)

    def preview_local_file(self, path):
        self.release_preview()
        extension = os.path.splitext(path)[1].lower()
        self.notice.setText("正在预览本机文件…")
        if extension in (".html", ".htm"):
            self.preview_local_html(path)
            return
        if extension == ".pdf":
            try:
                from PySide6.QtPdf import QPdfDocument
                from PySide6.QtPdfWidgets import QPdfView
                if not hasattr(self, "pdf_view"):
                    self.pdf_document = QPdfDocument(self)
                    self.pdf_buffer = QBuffer(self)
                    self.pdf_view = QPdfView(self)
                    self.pdf_view.setDocument(self.pdf_document)
                    self.pdf_view.setPageMode(QPdfView.PageMode.MultiPage)
                    self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
                    self.reader_stack.addWidget(self.pdf_view)
                with open(path, "rb") as source:
                    content = source.read(50 * 1024 * 1024 + 1)
                if len(content) > 50 * 1024 * 1024:
                    raise ValueError("PDF 超过 50 MB，请使用本机应用打开。")
                self.pdf_buffer.setData(QByteArray(content))
                self.pdf_buffer.open(QIODevice.ReadOnly)
                self.pdf_document.load(self.pdf_buffer)
                if self.pdf_document.error() != QPdfDocument.Error.None_:
                    raise RuntimeError(f"PDF 预览失败：{self.pdf_document.error()}")
                self.reader_stack.setCurrentWidget(self.pdf_view)
                self.notice.setText("本机 PDF 预览")
            except Exception as error:
                self.error(error)
            return
        if extension in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico"):
            try:
                if os.path.getsize(path) > 25 * 1024 * 1024:
                    raise ValueError("图片超过 25 MB，请使用本机应用打开。")
                pixmap = QPixmap(path)
                if pixmap.isNull():
                    raise ValueError("无法解码图片。")
                if not hasattr(self, "image_preview"):
                    self.image_preview = QLabel()
                    self.image_preview.setAlignment(Qt.AlignCenter)
                    self.reader_stack.addWidget(self.image_preview)
                self.image_preview.setPixmap(pixmap.scaled(self.reader_stack.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self.reader_stack.setCurrentWidget(self.image_preview)
                self.notice.setText("本机图片预览")
            except Exception as error:
                self.error(error)
            return
        def load():
            if extension in (".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls"):
                from core.deliverable_preview import render_structured_document_preview
                return "html", render_structured_document_preview(path)["html"]
            with open(path, "rb") as source:
                data = source.read(2 * 1024 * 1024 + 1)
            if len(data) > 2 * 1024 * 1024:
                raise ValueError("文件超过 2 MB 文本预览限制，请使用本机应用打开。")
            if b"\x00" in data:
                raise ValueError("此文件不是可预览文本，请使用本机应用打开。")
            return "markdown" if extension in (".md", ".markdown") else "text", data.decode("utf-8-sig")
        def done(result):
            if self.current_file != path:
                return
            kind, content = result
            if kind == "html":
                self.show_html(content.encode("utf-8"))
                self.notice.setText("本机 Office 内容预览")
            elif kind == "markdown":
                self.reader.document().setMarkdown(content, QTextDocument.MarkdownNoHTML)
                self.notice.setText("本机 Markdown 预览")
            else:
                self.reader.setPlainText(content)
                self.notice.setText("本机文本预览")
        self.run(load, done, "正在读取本机文件…")

    def preview_local_html(self, path):
        def load():
            with open(path, "rb") as source:
                content = source.read(1500001)
            if len(content) > 1500000:
                raise KnowledgeError("preview_too_large", "HTML 文件超过内置预览大小限制（1.5 MB）。")
            return content
        self.run(load, lambda content: self.show_html(content) if self.current_file == path else None,
                 "正在预览本机文件…")

    def show_html(self, content):
        from bs4 import UnicodeDammit
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings, QWebEngineUrlRequestInterceptor
        from PySide6.QtWebEngineWidgets import QWebEngineView
        decoded = UnicodeDammit(content, is_html=True).unicode_markup
        if decoded is None:
            raise KnowledgeError("preview_encoding", "无法识别 HTML 文件编码。")
        if not hasattr(self, "html_view"):
            class PreviewResources(QWebEngineUrlRequestInterceptor):
                def interceptRequest(self, info):
                    if info.requestUrl().scheme() not in ("data", "about"):
                        info.block(True)
            self.html_profile = QWebEngineProfile(self)
            self.html_interceptor = PreviewResources(self.html_profile)
            self.html_profile.setUrlRequestInterceptor(self.html_interceptor)
            self.html_view = QWebEngineView(self)
            page = QWebEnginePage(self.html_profile, self.html_view)
            self.html_view.setPage(page)
            settings = page.settings()
            settings.setAttribute(QWebEngineSettings.JavascriptEnabled, False)
            settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, False)
            settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, False)
            self.reader_stack.addWidget(self.html_view)
        self.html_view.setHtml(decoded, QUrl("about:blank"))
        self.reader_stack.setCurrentWidget(self.html_view)
        self.notice.clear()

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

    def checked_payloads(self):
        return [item.data(Qt.UserRole) for item in self.items._rows if item.checkState(0) == Qt.Checked]

    def check_page(self, checked):
        for item in self.items._rows:
            if item.flags() & Qt.ItemIsUserCheckable:
                item.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)

    def update_batch_selection(self):
        payloads = self.checked_payloads()
        refs = [p for p in payloads if "ref" in p or "document" in p]
        if hasattr(self, "batch_bar"):
            self.batch_bar.setVisible(bool(refs))
            self.batch_count.setText(f"已选择 {len(refs)} 份资料")
        self.use_button.setEnabled(bool(self.current_ref))
        self.use_button.setVisible(not refs and self.view == "files" and bool(self.current_ref))
        self.use_button.setText("和 Agent 一起用")
        if self.view == "artifacts":
            self.upload_button.hide()

    def use_reference(self, mode):
        refs = []
        if self.content_stack.currentIndex() == 0:
            for data in self.checked_payloads():
                ref = data.get("ref")
                if "document" in data:
                    doc = data["document"]
                    ref = self.service.reference(self.scope, doc["knowledge_base_id"], doc.get("title", doc["id"]), doc["id"])
                if ref and ref not in refs:
                    refs.append(ref)
        if not refs and self.current_ref:
            refs = [self.current_ref]
        if refs:
            self.referenceRequested.emit(copy.deepcopy(refs[0] if len(refs) == 1 else refs), mode)

    def upload_file(self):
        paths = [self.current_file] if self.current_file else [p["path"] for p in self.checked_payloads() if "path" in p]
        if not paths:
            paths, _ = QFileDialog.getOpenFileNames(self, "添加资料（可多选）")
        self.upload_paths(paths)

    def upload_paths(self, paths):
        paths = list(dict.fromkeys(os.path.abspath(path) for path in paths if path))
        if not paths:
            return
        scope = self.service.snapshot()
        if not scope:
            self.error(KnowledgeError("not_connected", "请先登录资料库。"))
            return
        self.scope = scope
        self.notice.setText("正在读取目标资料库…")
        def done(catalog):
            current = self.service.snapshot()
            if not current or not same_identity(scope, current) or scope.get("generation") != current.get("generation"):
                self.error(KnowledgeError("identity_changed", "账号或工作空间已变化，请重新选择上传目标。"))
                return
            self.choose_upload(paths, scope, catalog)
        # An explicit file submission must survive the page's initial catalog navigation.
        self.jobs.submit(lambda: self.service.catalog(scope), done, self.error)

    def choose_upload(self, paths, scope, catalog):
        paths = [paths] if isinstance(paths, str) else list(paths)
        dialog = QDialog(self)
        apply_product_dialog(dialog, "KnowledgeUploadDialog")
        dialog.setWindowTitle("保存到资料库")
        layout = QVBoxLayout(dialog)
        filename = QLabel(f"已选择 {len(paths)} 个文件")
        filename.setWordWrap(True)
        layout.addWidget(filename)
        files = QListWidget()
        files.setMaximumHeight(160)
        files.addItems(paths)
        layout.addWidget(files)
        targets = QComboBox()
        seen = set()
        for group in ("mine", "others", "shared"):
            for kb in catalog[group]:
                if kb["id"] in seen or not self.can_offer_upload(kb):
                    continue
                seen.add(kb["id"])
                targets.addItem(kb["name"], kb["id"])
        layout.addWidget(QLabel("目标资料库"))
        layout.addWidget(targets)
        if self.current_kb:
            index = targets.findData(self.current_kb["id"])
            if index >= 0:
                targets.setCurrentIndex(index)
        folder = QComboBox()
        folder.addItem("根目录", "")
        layout.addWidget(QLabel("文件夹"))
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
        self.submit_upload_batch(scope, paths, kb_id, folder_path)

    def submit_upload_batch(self, scope, paths, kb_id, folder_path):
        # Every file retains an independent durable receipt; never replay the whole batch.
        def work():
            results, errors = [], []
            for path in paths:
                try:
                    results.append(self.service.upload(scope, path, kb_id, folder_path))
                except Exception as error:
                    errors.append(os.path.basename(path) + "：" + str(error))
            return results, errors
        def done(result):
            if not self.scope or not same_identity(scope, self.scope):
                return
            results, errors = result
            self.notice.setText(f"已上传 {len(results)} / {len(paths)} 个文件。" + ("；".join(errors) if errors else "可在上传记录查看解析进度。"))
        self.jobs.submit(work, done, self.error)

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
                self.add_item(os.path.basename(task["path"]), {"task": task})
        self.notice.setText("正在核对上传记录…")
        self.poll_uploads(initial=True)

    def poll_uploads(self, initial=False):
        if self.upload_poll_busy or self.view != "uploads" or not self.scope or not self.isVisible():
            return
        tasks = [task for task in self.service.store.uploads()
                 if same_identity(task["scope"], self.scope) and ((initial and (task.get("knowledge_id") or task["status"] == "unknown")) or task["status"] in ("pending", "processing", "finalizing"))]
        if not tasks:
            if initial:
                self.notice.setText("暂无需要核对的上传；上传中的任务完成后会留下回执。" if self.items.count() else "还没有上传记录。")
            return
        self.upload_poll_busy = True
        scope = copy.deepcopy(self.scope)
        def work():
            errors = []
            for task in tasks:
                try:
                    self.service.check_upload(task)
                except Exception as error:
                    errors.append(str(error))
            return errors
        def done(errors):
            self.upload_poll_busy = False
            if self.view != "uploads" or not self.scope or not same_identity(scope, self.scope):
                return
            self.items.clear()
            for task in self.service.store.uploads():
                if same_identity(task["scope"], self.scope):
                    self.add_item(os.path.basename(task["path"]), {"task": task})
            self.notice.setText("状态核对失败：" + "；".join(dict.fromkeys(errors)) if errors else "已同步远端状态；解析中的记录会自动更新。")
        def failed(error):
            self.upload_poll_busy = False
            self.error(error)
        self.jobs.submit(work, done, failed)

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
