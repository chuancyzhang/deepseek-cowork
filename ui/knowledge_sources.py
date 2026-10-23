"""Source switcher and MCP pages reuse the native library reader and selection UI."""

import copy
import os
import time
from urllib.parse import urlencode

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMenu,
                              QListWidget, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from core.knowledge_library import KnowledgeError, same_identity
from core.knowledge_sources import SOURCES, MultiSourceKnowledgeService
from core.theme import bind_theme
from ui.knowledge_library import KnowledgePage, LibraryComboBox, LibraryJobs, STATUS
from ui.primitives import apply_product_dialog, product_button_style, product_field_style


STATUS.update({"saved": "已保存", "placing": "正在放入目标目录", "placement_pending": "已导入 · 正在放入目标目录",
               "placement_failed": "已导入 · 目标归位未完成", "action_required": "需要处理后继续"})


class MultiSourceKnowledgePage(QDialog):
    referenceRequested = Signal(object, str)

    def __init__(self, parent=None, service=None, artifacts=None):
        embedded = getattr(parent, "main_page_stack", None)
        super().__init__(embedded if embedded is not None else parent, Qt.Widget if embedded is not None else Qt.Dialog)
        self.service = service or MultiSourceKnowledgeService()
        self.artifacts = artifacts
        self.pages = {}
        self.source_views = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.sources = LibraryComboBox()
        self.sources.setAccessibleName("资料来源")
        self.sources.refresh_theme()
        for source, name in SOURCES.items():
            self.sources.addItem(name, source)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)
        self.sources.setCurrentIndex(self.sources.findData(self.service.source))
        self.sources.currentIndexChanged.connect(self.switch_source)
        self.switch_source()
        bind_theme(self, self.refresh_theme, surface="management")

    def page(self, source):
        if source not in self.pages:
            cls = KnowledgePage if source == "weknora" else CloudKnowledgePage
            page = cls(self, service=self.service.provider(source), artifacts=self.artifacts)
            page.setWindowFlags(Qt.Widget)
            page.referenceRequested.connect(self.referenceRequested.emit)
            # All upload entry points use the same dialog, including the old page.
            page.upload_paths = lambda paths, s=source: self.upload_paths(paths, source=s)
            self.pages[source] = page
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.NoFrame)
            scroll.setWidget(page)
            self.source_views[source] = scroll
            self.stack.addWidget(scroll)
        return self.pages[source]

    def switch_source(self, _index=0):
        source = self.sources.currentData()
        self.service.select_source(source)
        page = self.page(source)
        page.source_name.hide()
        page.source_slot.insertWidget(0, self.sources)
        self.sources.show()
        self.stack.setCurrentWidget(self.source_views[source])

    def closeEvent(self, event):
        for page in self.pages.values():
            if page._auth_cancel:
                page._auth_cancel.set()
        super().closeEvent(event)

    def upload_paths(self, paths, source=None):
        paths = list(dict.fromkeys(os.path.abspath(p) for p in paths if p))
        if not paths:
            return
        last = self.service.store.setting("last_upload_target") or {}
        selected = source or last.get("source") or self.service.source
        target = last if source is None else {}
        if source and source in self.pages and self.pages[source].current_kb:
            page = self.pages[source]
            target = {"source": source, "collection_id": page.current_kb["id"], "folder": page.folder or ""}
        dialog = KnowledgeUploadDialog(self, self.service, paths, selected, target)
        if dialog.exec() == QDialog.Accepted:
            provider, scope, collection, folder = dialog.selection
            page = self.page(provider.source)
            page.scope = scope
            page.submit_upload_batch(scope, paths, collection, folder)
            self.sources.setCurrentIndex(self.sources.findData(provider.source))

    def refresh_theme(self, *args):
        self.sources.refresh_theme()
        for page in self.pages.values():
            page.refresh_theme(*args)


class CloudKnowledgePage(KnowledgePage):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.parents = []
        self._has_next = False
        self._current_task = None
        self.login_box.hide()
        self.login_box = QWidget()
        self.login_box.setMaximumWidth(560)
        layout = QVBoxLayout(self.login_box)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("连接" + self.service.display_name)
        title.setObjectName("LibraryHeading")
        layout.addWidget(title)
        help_text = ("在腾讯文档使用 QQ 或微信授权，完成后返回并点击“已完成授权”。链接有效期 5 分钟。"
                     if self.service.source == "tencent-docs" else
                     "在乐享官方配置页获取企业标识和 Token，填写后验证连接。Token 仅加密保存在本机。")
        help_label = QLabel(help_text)
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        self.company = QLineEdit()
        self.company.setPlaceholderText("官方配置页中的企业标识")
        self.token = QLineEdit()
        self.token.setPlaceholderText("官方配置页中的 Token")
        self.token.setEchoMode(QLineEdit.Password)
        if self.service.source == "lexiang":
            layout.addWidget(QLabel("企业标识"))
            layout.addWidget(self.company)
            layout.addWidget(QLabel("访问令牌"))
            layout.addWidget(self.token)
        actions = QHBoxLayout()
        self.connect_button = QPushButton("打开授权页面" if self.service.source == "tencent-docs" else "连接")
        self.connect_button.setProperty("libraryPrimary", True)
        self.connect_button.clicked.connect(self.connect_source)
        actions.addWidget(self.connect_button)
        self.cancel_login_button.setParent(self.login_box)
        actions.addWidget(self.cancel_login_button)
        self.confirm_button = QPushButton("已完成授权")
        self.confirm_button.clicked.connect(self.finish_authorization)
        self.confirm_button.setEnabled(False)
        self.confirm_button.setVisible(self.service.source == "tencent-docs")
        actions.addWidget(self.confirm_button)
        official = QPushButton("打开官方配置页")
        official.clicked.connect(self.open_official)
        official.setVisible(self.service.source == "lexiang")
        actions.addWidget(official)
        actions.addStretch()
        layout.addLayout(actions)
        self.body_layout.insertWidget(0, self.login_box)
        # Replace the old account menu; it contains WeKnora-only actions.
        old_menu = self.account_button.menu()
        old_menu.removeAction(self.account_details_action)
        self.tenants.hide()
        self.account_label.show()
        self.account_label.setWordWrap(True)
        account_button = self.account_button
        account_menu = QMenu(account_button)
        self.account_details_action.setParent(account_menu)
        account_menu.addAction(self.account_details_action)
        account_menu.addSeparator()
        for title, callback in (("检查连接", self.check_connection), ("重新连接", self.reconnect),
                                ("官方页面 / 续期", self.open_official), ("断开连接", self.logout)):
            account_menu.addAction(title, callback)
        account_button.setMenu(account_menu)
        old_menu.deleteLater()
        self.search_scope.hide()
        self.query.setPlaceholderText("搜索当前来源" + ("的文档标题" if self.service.source == "tencent-docs" else "的资料"))
        self.up_button = QPushButton("上一级")
        self.up_button.clicked.connect(self.go_up)
        self.search_toolbar.layout.addWidget(self.up_button)
        self.up_button.hide()
        self.resume_button = QPushButton("继续放入目标目录")
        self.resume_button.clicked.connect(self.resume_placement)
        self.resume_button.hide()
        self.body_layout.addWidget(self.resume_button)
        self.remote_button = QPushButton("在原平台查看")
        self.remote_button.clicked.connect(self.open_management)
        self.remote_button.hide()
        self.body_layout.addWidget(self.remote_button)
        for button in self.findChildren(QPushButton):
            menu = button.menu()
            if menu:
                for action in menu.actions():
                    if action.text() == "在 WeKnora 中打开":
                        action.setText("在" + self.service.display_name + "中打开")
        self.refresh_theme()

    def open_official(self):
        url = "https://docs.qq.com/home" if self.service.source == "tencent-docs" else "https://lexiangla.com/mcp"
        if self.scope and self.service.source == "lexiang":
            url += "?" + urlencode({"company_from": self.scope["tenant_id"]})
        QDesktopServices.openUrl(QUrl(url))

    def connect_source(self):
        self.connect_button.setEnabled(False)
        if self.service.source == "tencent-docs":
            def done(url):
                self.connect_button.setEnabled(True)
                self.confirm_button.setEnabled(True)
                self.cancel_login_button.show()
                QDesktopServices.openUrl(QUrl(url))
                self.notice.setText("请完成浏览器中的授权，再点击“已完成授权”。")
            self.run_authentication(lambda cancelled: self.service.start_authorization(cancelled=cancelled), done, "正在准备授权…")
        else:
            company, token = self.company.text(), self.token.text()
            def done(_):
                self.token.clear()
                self.connect_button.setEnabled(True)
                self.refresh()
            self.run_authentication(lambda cancelled: self.service.connect(company, token, cancelled=cancelled), done, "正在验证企业与账号…")

    def finish_authorization(self):
        self.confirm_button.setEnabled(False)
        def done(_):
            self.refresh()
        self.run_authentication(lambda cancelled: self.service.finish_authorization(confirmed=True, cancelled=cancelled), done, "正在核对授权结果…", continuing=True)

    def reconnect(self):
        self.login_box.show()
        self.connect_button.setEnabled(True)
        self.notice.setText("新连接验证成功后才会替换现有连接；原有资料选择会保留。")

    def check_connection(self):
        def done(_):
            self.account_label.setText("已连接 · " + self.scope.get("email", self.service.display_name))
            self.notice.setText("连接有效，可以继续使用。")
        self.run(self.service.verify, done, "正在检查连接…")

    def error(self, error):
        self.notice.setText(str(error))
        self.retry_state_button.setVisible(getattr(error, "code", "") == "retirement_failed")
        if self.account_label.text() == "凭据已保存 · 正在检查连接":
            self.account_label.setText("凭据已保存 · 连接检查或资料加载未完成")
        if hasattr(self, "connect_button"):
            self.connect_button.setEnabled(True)
            self.confirm_button.setEnabled(True)
        if getattr(error, "code", "") in ("unauthenticated", "not_connected", "retirement_failed"):
            self.account_label.setText("授权失效 · " + self.service.display_name)
            self.login_box.show()

    def clear_view(self):
        super().clear_view()
        if hasattr(self, "up_button"):
            self._current_task = None
            self.up_button.hide()
            self.resume_button.hide()
            self.remote_button.hide()

    def refresh(self):
        self.clear_view()
        self._directory_expansion = []
        self.tree.clear()
        self.current_kb = None
        self.catalog_names = {}
        self.scope = self.service.snapshot()
        self.retry_state_button.setVisible(bool(self.scope and self.scope.get("error")))
        connected = bool(self.scope) and not self.scope.get("unavailable")
        self.login_box.setVisible(not connected)
        self.account_bar.setVisible(connected)
        self.splitter.setVisible(connected)
        self.navigation.setVisible(connected)
        self.navigation_spacer.setVisible(not connected)
        self.login_spacer.setVisible(not connected)
        if not connected:
            from core.connection_retirement import library_notice
            self.notice.setText((self.scope or {}).get("error") or library_notice(self.service.store.data_dir, self.service.source))
            return
        self.account_label.setText("凭据已保存 · 正在检查连接")
        scope = copy.deepcopy(self.scope)
        def load():
            self.service.verify()
            return self.service.catalog(scope)
        self.run(load, self.loaded_cloud)

    def refresh_content(self):
        self.service.clear_cache()
        if self.scope and self.view == "files" and self.current_kb:
            self.clear_view()
            self.load_files()
        elif self.scope and self.view == "search":
            self.search()
        else:
            self.refresh()

    def loaded_cloud(self, catalog):
        self.account_label.setText("已连接 · " + " · ".join(filter(None, [self.scope.get("tenant_name"), self.scope.get("email")])))
        self.catalog_names = {str(kb["id"]): kb["name"] for key in ("mine", "others", "shared") for kb in catalog[key]}
        for title, kind in (("最近访问", "recent"), ("本地文件", "artifacts"), ("上传记录", "uploads")):
            self.tree.addTopLevelItem(self.node(title, {"kind": kind}))
        for title, key in (("我的文件", "mine"), ("知识库空间", "others")):
            if not catalog[key]:
                continue
            parent = self.node(title, {"kind": "group"})
            self.tree.addTopLevelItem(parent)
            for kb in catalog[key]:
                parent.addChild(self.node(kb["name"], {"kind": "kb", "kb": kb}))
            parent.setExpanded(True)
        for org in catalog["organizations"]:
            parent = self.node(org["name"], {"kind": "group"})
            self.tree.addTopLevelItem(parent)
            for kb in catalog["shared"]:
                if kb["organization_id"] == org["id"]:
                    parent.addChild(self.node(kb["name"], {"kind": "kb", "kb": kb}))
            parent.setExpanded(True)
        self.finish_navigation()

    def open_kb(self, kb):
        self.clear_view()
        self.current_kb = kb
        self.select_directory(kb)
        self.parents = []
        self.folder, self.page, self.view = "", 1, "files"
        self.load_files()

    def load_files(self):
        scope, collection, folder, page = copy.deepcopy(self.scope), self.current_kb["id"], self.folder or "", self.page
        self.run(lambda: self.service.children(scope, collection, folder, page), self.loaded_cloud_files)

    def loaded_cloud_files(self, result):
        self.items.clear()
        values, self._has_next = result
        self.title.setText(self.current_kb["name"] + (" / " + " / ".join(p[1] for p in self.parents) if self.parents else ""))
        self.current_ref = self.service.reference(self.scope, self.current_kb["id"], self.current_kb["name"])
        for control in (self.use_button, self.upload_button, self.management_button, self.previous, self.next):
            control.show()
        self.up_button.setVisible(bool(self.parents))
        self.use_button.setEnabled(True)
        self.upload_button.setEnabled(True)
        self.update_pagination(self._has_next)
        for value in values:
            if value["is_folder"]:
                self.add_item(value["title"], {"folder": value})
            else:
                ref = self.service.reference(self.scope, value["knowledge_base_id"], value["title"], value["id"], url=value["url"])
                self.add_item(value["title"], {"ref": ref, "document": value})
        self.notice.setText("" if values else "此目录暂无资料。可添加资料或返回上一级。")

    def go_up(self):
        self.clear_view()
        if self.parents:
            self.folder, _ = self.parents.pop()
        self.page = 1
        self.load_files()

    def select_item(self, item, _column=0):
        data = item.data(Qt.UserRole) or {}
        if "folder" in data:
            self.clear_view()
            value = data["folder"]
            self.parents.append((self.folder or "", value["title"]))
            self.folder, self.page = value["id"], 1
            self.load_files()
            return
        self._current_task = data.get("task")
        self.resume_button.hide()
        self.remote_button.hide()
        if self._current_task:
            task = self._current_task
            self.release_preview()
            self.content_stack.setCurrentIndex(1)
            self.current_ref, self.current_file = None, task["path"]
            self.reader_title.setText(os.path.basename(task["path"]))
            self.reader_use.hide()
            self.reader_save.hide()
            self.read_more.hide()
            if os.path.isfile(task["path"]):
                self.preview_local_file(task["path"])
            else:
                self.reader_stack.setCurrentIndex(0)
                self.reader.setPlainText("本机文件已移动或删除，远端上传记录仍保留。")
            def done(result):
                if self._current_task is not task:
                    return
                self.resume_button.setVisible(result["status"] == "placement_failed")
                self.resume_button.setEnabled(True)
                self.remote_button.setVisible(bool(result.get("url")))
                self.notice.setText(STATUS.get(result["status"], result["status"]) + " " + result.get("error", ""))
            self.run(lambda: self.service.check_upload(task), done, "正在核对上传记录…")
            return
        if "ref" in data and data["ref"].get("item_id"):
            ref = data["ref"]
            self.release_preview()
            self.content_stack.setCurrentIndex(1)
            self.reader_stack.setCurrentIndex(0)
            self.reader_title.setText(ref["title"])
            self.reader_use.show()
            self.reader_save.hide()
            self.current_ref, self.current_file, self.read_page = ref, "", 1
            self.reader.clear()
            self.read_more.hide()
            self.read_ref(ref)
        else:
            super().select_item(item, _column)
        self.resume_button.setVisible(bool(self._current_task and self._current_task["status"] == "placement_failed"))

    def resume_placement(self):
        task = copy.deepcopy(self._current_task)
        self.resume_button.setEnabled(False)
        def done(result):
            self.resume_button.setEnabled(True)
            self.resume_button.setVisible(result["status"] == "placement_failed")
            self.notice.setText(STATUS.get(result["status"], result["status"]) + " " + result.get("error", ""))
        def failed(error):
            self.resume_button.setEnabled(True)
            self.error(error)
        self.jobs.submit(lambda: self.service.check_upload(task, resume=True), done, failed)

    def search(self):
        query = self.query.text().strip()
        if not query or not self.scope:
            self.notice.setText("请先连接并输入关键词。")
            return
        self.clear_view()
        self.view = "search"
        self.title.setText("搜索结果")
        scope = copy.deepcopy(self.scope)
        def done(values):
            for value in values:
                ref = self.service.reference(scope, value["knowledge_base_id"], value["title"], value["id"], url=value["url"])
                self.add_item(value["title"], {"ref": ref, "document": value})
            self.notice.setText("" if values else "没有匹配资料，请调整关键词。")
        self.run(lambda: self.service.search(scope, query), done, "正在搜索当前来源…")

    def open_management(self, path="", connected=True):
        if self.current_ref and self.current_ref.get("url"):
            self.open_source(QUrl(self.current_ref["url"]))
        elif self._current_task and self._current_task.get("url"):
            self.open_source(QUrl(self._current_task["url"]))
        else:
            self.open_official()

    def poll_uploads(self, initial=False):
        # Tencent import is asynchronous and still requires placement. Keep this
        # work running when the user leaves the upload list or changes sources.
        if self.upload_poll_busy or not self.scope:
            return
        scope = copy.deepcopy(self.scope)
        tasks = [t for t in self.service.store.uploads() if same_identity(t["scope"], scope) and
                 (t["status"] in {"processing", "placement_pending"} or
                  (initial and t["status"] not in {"rejected", "failed", "saved"}))]
        if not tasks:
            if initial:
                self.notice.setText("" if self.items.count() else "还没有上传记录。可在资料目录中添加资料。")
            return
        self.upload_poll_busy = True
        def work():
            for task in tasks:
                try:
                    if self.service.active_upload(task):
                        continue
                    started = task.get("created_at", task["updated_at"])
                    if not initial and time.time() - started > 300:
                        self.service.pause_upload(task, "导入耗时较长，已暂停自动检查。点击记录可继续核对，无需重新上传。")
                        continue
                    self.service.check_upload(task)
                except KnowledgeError as error:
                    self.service.pause_upload(task, str(error))
            return None
        def done(_):
            self.upload_poll_busy = False
            if self.view == "uploads" and self.scope and same_identity(scope, self.scope):
                self.items.clear()
                for task in self.service.store.uploads():
                    if same_identity(task["scope"], scope):
                        self.add_item(os.path.basename(task["path"]), {"task": task})
                self.notice.clear()
        def failed(error):
            self.upload_poll_busy = False
            self.error(error)
        self.jobs.submit(work, done, failed)


class KnowledgeUploadDialog(QDialog):
    """One save boundary for every entry point and source; reads never submit files."""

    def __init__(self, parent, service, paths, source, last):
        super().__init__(parent)
        apply_product_dialog(self, "KnowledgeUploadDialog")
        self.setWindowTitle("保存到资料库")
        self.service, self.paths, self.last = service, paths, last
        self.jobs = LibraryJobs(self)
        self.epoch, self.alive = 0, True
        self.scope, self.selection = None, None
        self.parents, self.parent_id = [], ""
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"已选择 {len(paths)} 个文件"))
        files = QListWidget()
        files.addItems([os.path.basename(p) for p in paths])
        files.setMaximumHeight(120)
        layout.addWidget(files)
        self.sources = QComboBox()
        for name, label in SOURCES.items():
            self.sources.addItem(label, name)
        self.targets = QComboBox()
        self.folders = QComboBox()
        for label, field in (("资料来源", self.sources), ("目标资料库", self.targets), ("目标文件夹", self.folders)):
            layout.addWidget(QLabel(label))
            field.setStyleSheet(product_field_style())
            layout.addWidget(field)
        navigation = QHBoxLayout()
        self.enter = QPushButton("进入文件夹")
        self.enter.clicked.connect(self.enter_folder)
        self.up = QPushButton("上一级")
        self.up.clicked.connect(self.up_folder)
        navigation.addWidget(self.enter)
        navigation.addWidget(self.up)
        layout.addLayout(navigation)
        self.note = QLabel()
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        buttons = QHBoxLayout()
        self.submit = QPushButton("上传")
        self.submit.setStyleSheet(product_button_style("primary"))
        self.submit.clicked.connect(self.commit)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(self.submit)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)
        self.sources.setCurrentIndex(max(0, self.sources.findData(source)))
        self.sources.currentIndexChanged.connect(self.load_source)
        self.targets.currentIndexChanged.connect(self.load_target)
        self.finished.connect(self.finish)
        self.load_source()
        self.resize(min(560, max(320, self.screen().availableGeometry().width() - 48)), self.sizeHint().height())
        bind_theme(self, self.refresh_theme, surface="management")

    def refresh_theme(self, _resolved=None):
        for field in (self.sources, self.targets, self.folders):
            field.setStyleSheet(product_field_style())
        for button in self.findChildren(QPushButton):
            button.setStyleSheet(product_button_style("primary" if button is self.submit else "secondary"))

    def finish(self, _result):
        self.alive = False
        self.epoch += 1

    def run(self, work, done):
        self.epoch += 1
        epoch = self.epoch
        self.submit.setEnabled(False)
        self.note.setText("正在读取目标位置…")
        def failed(error):
            if self.alive and epoch == self.epoch:
                self.note.setText(str(error))
        self.jobs.submit(work, lambda result: done(result) if self.alive and epoch == self.epoch else None, failed)

    def load_source(self):
        self.provider = self.service.provider(self.sources.currentData())
        self.scope = self.provider.snapshot()
        self.targets.blockSignals(True)
        self.targets.clear()
        self.targets.blockSignals(False)
        self.folders.clear()
        if not self.scope:
            self.epoch += 1
            self.submit.setEnabled(False)
            self.note.setText("此来源尚未连接。请先在资料库页面完成连接。")
            return
        provider, scope = self.provider, copy.deepcopy(self.scope)
        def done(catalog):
            self.targets.blockSignals(True)
            seen = set()
            for group in ("mine", "others", "shared"):
                for kb in catalog[group]:
                    if kb["id"] not in seen and kb.get("permission") != "viewer" and not kb.get("source_from_agent"):
                        seen.add(kb["id"])
                        self.targets.addItem(kb["name"], kb["id"])
            if self.last.get("source") == provider.source:
                index = self.targets.findData(self.last.get("collection_id"))
                if index >= 0:
                    self.targets.setCurrentIndex(index)
            self.targets.blockSignals(False)
            self.load_target()
        self.run(lambda: provider.catalog(scope), done)

    def load_target(self):
        self.parents, self.parent_id = [], ""
        if self.last.get("source") == self.provider.source and self.last.get("collection_id") == self.targets.currentData():
            self.parent_id = self.last.get("folder") or ""
        self.load_folders()

    def load_folders(self):
        provider, scope, collection, parent = self.provider, copy.deepcopy(self.scope), self.targets.currentData(), self.parent_id
        self.folders.clear()
        if not collection:
            self.epoch += 1
            self.submit.setEnabled(False)
            self.note.setText("没有可用的目标资料库，请在原平台创建或申请权限。")
            return
        cloud = provider.source != "weknora"
        self.enter.setVisible(cloud)
        self.up.setVisible(cloud)
        self.up.setEnabled(bool(self.parents) or bool(parent))
        def work():
            if not cloud:
                return provider.folders(scope, collection)
            folders, page = [], 1
            while True:
                values, more = provider.children(scope, collection, parent, page)
                folders.extend(i for i in values if i["is_folder"])
                if not more:
                    break
                page += 1
                if page > 1000:
                    raise KnowledgeError("pagination_limit", "目录过大，请在原平台整理后再选择。")
            return folders
        def done(data):
            self.folders.addItem("当前目录" if parent else "根目录", parent)
            if cloud:
                for item in data:
                    self.folders.addItem(item["title"], item["id"])
            else:
                self.folders.setItemData(0, "")
                def visit(nodes, depth=0):
                    for node in nodes:
                        self.folders.addItem("  " * depth + node["name"], node["path"])
                        visit(node.get("children") or [], depth + 1)
                visit(data.get("folders") or [])
                index = self.folders.findData(parent)
                if index >= 0:
                    self.folders.setCurrentIndex(index)
            self.submit.setEnabled(True)
            self.note.setText("保存位置：" + SOURCES[provider.source] + " / " + self.targets.currentText() +
                              (" / " + " / ".join(p[1] for p in self.parents) if self.parents else "") +
                              "。权限由服务验证，不覆盖已有文件。")
        self.run(work, done)

    def enter_folder(self):
        selected = self.folders.currentData()
        if selected and selected != self.parent_id:
            self.parents.append((self.parent_id, self.folders.currentText()))
            self.parent_id = selected
            self.load_folders()

    def up_folder(self):
        self.parent_id = self.parents.pop()[0] if self.parents else ""
        self.load_folders()

    def commit(self):
        current = self.provider.snapshot()
        if not current or not same_identity(current, self.scope) or current.get("generation") != self.scope.get("generation"):
            self.submit.setEnabled(False)
            self.note.setText("连接身份已变化，请重新选择来源和目标。")
            return
        self.selection = (self.provider, copy.deepcopy(self.scope), self.targets.currentData(), self.folders.currentData() or "")
        self.accept()
