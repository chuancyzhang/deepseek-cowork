"""Settings only; importing the panel never loads a scanner or token vault."""

from PySide6.QtCore import Signal, QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QCheckBox, QLabel, QPushButton, QHBoxLayout
from core.data_security import CATEGORIES, FEATURES, normalize_config, recent_events, validate_config
from core.theme import DesignTokens, bind_theme
from ui.primitives import ProductSection, product_button_style
from .records import SecurityRecords


class DataSecurityPanel(QWidget):
    changed = Signal()

    def __init__(self, value=None, parent=None):
        super().__init__(parent)
        self.setObjectName("DataSecurityPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        intro = QLabel("随应用附带的可选插件，默认全部关闭。用于减少意外暴露，不保证拦截所有数据外发。")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.enabled = QCheckBox("启用数据安全插件")
        layout.addWidget(self.enabled)
        self.options = QWidget()
        options = QVBoxLayout(self.options)
        options.setContentsMargins(0, 0, 0, 0)
        options.setSpacing(16)
        features = ProductSection("选择功能", "开启插件不会自动开启以下功能。保存后从下一次新启动任务生效。", kind="plain")
        self.checks = {}
        for key, label in FEATURES.items():
            check = QCheckBox(label)
            check.setObjectName("security_" + key)
            features.layout.addWidget(check)
            self.checks[key] = check
            if key == "credential_warning":
                hint = QLabel("检查 API Key、密码等凭证；手机号、邮箱不触发此提醒。保存后对新任务生效，提醒不阻断发送。")
                hint.setWordWrap(True)
                features.layout.addWidget(hint)
        options.addWidget(features)
        self.validation = QLabel()
        self.validation.setObjectName("SecurityValidation")
        self.validation.setWordWrap(True)
        options.addWidget(self.validation)
        types = ProductSection("令牌化的数据类别", "只转换发给模型的用户输入、附件提取文本和工具结果；AI 回复不做脱敏。", kind="plain")
        self.categories = {}
        for key, label in CATEGORIES.items():
            check = QCheckBox(label)
            check.setObjectName("security_category_" + key)
            types.layout.addWidget(check)
            self.categories[key] = check
        options.addWidget(types)
        note = QLabel("原始消息和文件保持不变。无法脱敏或任务需要真实内容时，会提示并使用原文继续。图片及第三方工具自行发送的内容不在保护范围内。")
        note.setWordWrap(True)
        options.addWidget(note)
        layout.addWidget(self.options)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        records_section = ProductSection("安全记录", "了解最近的处理结果，需要时再查看详情。记录保存在本机，旧记录会自动轮换。", kind="plain")
        self.refresh_button = QPushButton("刷新记录")
        self.refresh_button.clicked.connect(self.refresh_events)
        records_section.layout.addWidget(self.refresh_button)
        self.events = SecurityRecords()
        records_section.layout.addWidget(self.events)
        self.check_status = QLabel()
        self.check_status.setWordWrap(True)
        records_section.layout.addWidget(self.check_status)
        actions = QHBoxLayout()
        self.retry_button = QPushButton("重新检查 Skill / MCP")
        self.retry_button.setToolTip("按已保存的设置，重新检查本次打开应用后检查过的项目。")
        self.retry_button.clicked.connect(self.retry_checks)
        actions.addWidget(self.retry_button)
        self.cancel_button = QPushButton("停止检查")
        self.cancel_button.clicked.connect(self.cancel_checks)
        self.cancel_button.hide()
        actions.addWidget(self.cancel_button)
        actions.addStretch()
        records_section.layout.addLayout(actions)
        layout.addWidget(records_section)
        attribution = QLabel('规则参考：<a href="https://github.com/Tencent/AI-Infra-Guard">腾讯朱雀实验室 AI-Infra-Guard</a>')
        attribution.setOpenExternalLinks(True)
        attribution.setWordWrap(True)
        layout.addWidget(attribution)
        layout.addStretch()
        for check in [self.enabled, *self.checks.values(), *self.categories.values()]:
            check.toggled.connect(self._changed)
        self.set_value(value)
        self.refresh_theme()
        bind_theme(self, self.refresh_theme, surface="management")
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh_check_controls)

    def refresh_theme(self, _resolved=None):
        self.setStyleSheet(f"QWidget#DataSecurityPanel QLabel, QWidget#DataSecurityPanel QCheckBox {{ color: {DesignTokens.text_primary}; }} "
                          f"QLabel#SecurityValidation {{ color: {DesignTokens.error_text}; }}")
        self.events.render()
        self.validation.setStyleSheet(f"QLabel#SecurityValidation {{ color: {DesignTokens.error_text}; font-weight: 600; }}")
        for button in (self.refresh_button, self.cancel_button, self.retry_button):
            button.setStyleSheet(product_button_style("secondary"))

    def state(self):
        return {"enabled": self.enabled.isChecked(),
                **{key: check.isChecked() for key, check in self.checks.items()},
                "categories": {key: check.isChecked() for key, check in self.categories.items()}}

    def set_value(self, value):
        value = normalize_config(value)
        self.enabled.setChecked(value["enabled"])
        for key, check in self.checks.items():
            check.setChecked(value[key])
        for key, check in self.categories.items():
            check.setChecked(value["categories"][key])
        self._changed()

    def validate(self, *, reveal=True):
        try:
            validate_config(self.state())
        except ValueError as exc:
            self.validation.setText(str(exc))
            if reveal:
                from PySide6.QtWidgets import QScrollArea
                parent = self.parentWidget()
                while parent is not None:
                    if isinstance(parent, QScrollArea):
                        parent.ensureWidgetVisible(self.validation, 0, 32)
                        break
                    parent = parent.parentWidget()
            return False
        return True

    def _changed(self):
        self.options.setVisible(self.enabled.isChecked())
        self.validation.setText("")
        self.validate(reveal=False)
        self.status.setText("未启用：不会新增扫描或脱敏。" if not self.enabled.isChecked()
                            else "仅处理明确选择的功能；更改需保存后生效。" if any(c.isChecked() for c in self.checks.values())
                            else "尚未选择功能，不会启动安全处理。")
        self.changed.emit()

    def refresh_events(self):
        try:
            from .events import read_recent
            events = read_recent() + recent_events()
        except (OSError, ValueError):
            import time
            events = recent_events() + [{"event": "log", "status": "failed", "timestamp": time.time(),
                "message": "已保存的记录暂时无法读取，请稍后刷新；任务不受影响。"}]
        self.events.set_records(events)
        self.refresh_check_controls()

    def refresh_check_controls(self):
        # No scanner import and no disk IO during polling. Browsing stays stable.
        import sys
        scanner = sys.modules.get("bundled_plugins.data_security.capabilities")
        activity = scanner.activity() if scanner is not None else {"pending": 0, "recent": 0}
        pending = activity["pending"]
        self.cancel_button.setVisible(bool(pending))
        self.retry_button.setEnabled(bool(activity["recent"]) and not pending)
        self.check_status.setText(f"{pending} 项检查正在进行或等待中。完成后点击“刷新记录”查看结果。" if pending
            else "可重新检查本次打开应用后检查过的 Skill / MCP；使用已保存的设置。" if activity["recent"]
            else "暂无可重新检查的项目。启用并保存检查功能后，安装、更新 Skill 或连接 MCP 时会进行检查。")

    def cancel_checks(self):
        import sys
        scanner = sys.modules.get("bundled_plugins.data_security.capabilities")
        if scanner is not None:
            scanner.cancel_all()
        self.refresh_check_controls()
        self.refresh_events()

    def retry_checks(self):
        # Use saved configuration, never an uncommitted settings draft.
        import sys
        from core.config_manager import ConfigManager
        from core.data_security import publish
        scanner = sys.modules.get("bundled_plugins.data_security.capabilities")
        if scanner is None:
            publish({"event": "scan", "status": "inactive", "message": "暂无可重试的检查。保存检查设置后，安装或更新 Skill、保存或连接 MCP 可触发检查。"})
            self.refresh_events()
            return
        scanner.retry_recent(ConfigManager().get_data_security())
        self.refresh_events()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_events()
        self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)
