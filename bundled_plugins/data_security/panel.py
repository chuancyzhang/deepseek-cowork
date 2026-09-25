"""Settings only; importing the panel never loads a scanner or token vault."""
import json
from datetime import datetime

from PySide6.QtCore import Signal, QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QCheckBox, QLabel, QPushButton, QPlainTextEdit
from core.data_security import CATEGORIES, FEATURES, normalize_config, recent_events, validate_config
from core.theme import DesignTokens, bind_theme
from ui.primitives import ProductSection, product_code_style, product_button_style


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
        note = QLabel("原始消息和文件不修改。检查失败或复杂操作需要真实内容时，会提示并按原文继续；正常鉴权不受影响。系统指令、工具定义、图片及脚本内部外发不在脱敏范围内。改变令牌化设置可能影响模型历史缓存命中。")
        note.setWordWrap(True)
        options.addWidget(note)
        layout.addWidget(self.options)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.refresh_button = QPushButton("查看／刷新安全记录")
        self.refresh_button.clicked.connect(self.refresh_events)
        layout.addWidget(self.refresh_button)
        self.events = QPlainTextEdit()
        self.events.setReadOnly(True)
        self.events.setMinimumHeight(140)
        self.events.setMaximumHeight(260)
        self.events.setPlaceholderText("暂无安全事件。所有功能默认关闭，不会自动扫描。")
        self.events.hide()
        layout.addWidget(self.events)
        self.retry_button = QPushButton("重新检查最近的能力")
        self.retry_button.clicked.connect(self.retry_checks)
        self.retry_button.hide()
        layout.addWidget(self.retry_button)
        self.cancel_button = QPushButton("取消后台检查")
        self.cancel_button.clicked.connect(self.cancel_checks)
        self.cancel_button.hide()
        layout.addWidget(self.cancel_button)
        attribution = QLabel("Based on Tencent Zhuque Lab AI-Infra-Guard\nhttps://github.com/Tencent/AI-Infra-Guard")
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
        self.timer.timeout.connect(self.refresh_events)

    def refresh_theme(self, _resolved=None):
        self.setStyleSheet(f"QWidget#DataSecurityPanel QLabel, QWidget#DataSecurityPanel QCheckBox {{ color: {DesignTokens.text_primary}; }} "
                          f"QLabel#SecurityValidation {{ color: {DesignTokens.error_text}; }}")
        self.events.setStyleSheet(product_code_style())
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
        if self.sender() is self.refresh_button:
            self.events.show()
            self.cancel_button.show()
            self.retry_button.show()
        lines = []
        try:
            from .events import read_recent
            events = read_recent() + recent_events()
        except (OSError, ValueError):
            events = recent_events() + [{"message": "已保存的安全日志暂时无法读取；可稍后刷新，任务不受影响。"}]
        seen = set()
        unique = []
        for event in sorted(events, key=lambda e: e.get("timestamp", 0)):
            key = (event.get("event"), event.get("run_id"), event.get("object_id"), event.get("status"), event.get("message"))
            if key not in seen:
                unique.append(event)
                seen.add(key)
        for event in unique[-60:]:
            stamp = datetime.fromtimestamp(event.get("timestamp", 0)).strftime("%H:%M:%S")
            lines.append(f'{stamp} · {event.get("object_id", event.get("event", ""))} · {event.get("message", "")}')
            for finding in event.get("findings", []):
                lines.append(f'  {finding["file"]}:{finding["line"]} · {finding["rule"]}')
            if event.get("coverage"):
                lines.append("  " + event["coverage"])
            if event.get("error_type"):
                lines.append("  不可用原因：" + event["error_type"])
        text = "\n".join(lines)
        if text != self.events.toPlainText():
            self.events.setPlainText(text)

    def cancel_checks(self):
        import sys
        scanner = sys.modules.get("bundled_plugins.data_security.capabilities")
        if scanner is not None:
            scanner.cancel_all()

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

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_events()
        self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)
