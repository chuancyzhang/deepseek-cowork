"""Host-owned settings entry: missing plugin UI must not break Settings."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton
from core.data_security import normalize_config, publish
from core.theme import DesignTokens, bind_theme
from ui.primitives import product_button_style


class UnavailableSecurityPanel(QWidget):
    changed = Signal()

    def __init__(self, value, parent=None, error_type=""):
        super().__init__(parent)
        self.value = normalize_config(value)
        layout = QVBoxLayout(self)
        label = QLabel("数据安全插件不可用。现有功能仍可正常使用；请修复应用安装包后重试，或关闭插件。" +
                       ("\n原因：" + error_type if error_type else ""))
        label.setObjectName("SecurityUnavailable")
        label.setWordWrap(True)
        layout.addWidget(label)
        self.disable = QPushButton("关闭插件")
        self.disable.clicked.connect(self._disable)
        layout.addWidget(self.disable)
        bind_theme(label, lambda _resolved=None: label.setStyleSheet(
            f"QLabel#SecurityUnavailable {{ color: {DesignTokens.text_primary}; }}"), surface="management")
        bind_theme(self.disable, lambda _resolved=None: self.disable.setStyleSheet(product_button_style("secondary")), surface="management")

    def _disable(self):
        self.value["enabled"] = False
        self.changed.emit()

    def state(self):
        return normalize_config(self.value)

    def set_value(self, value):
        self.value = normalize_config(value)

    def validate(self):
        return True


def create_data_security_panel(value, parent=None):
    try:
        from bundled_plugins.data_security.panel import DataSecurityPanel
        return DataSecurityPanel(value, parent)
    except Exception as exc:
        publish({"event": "plugin_ui", "status": "failed", "error_type": type(exc).__name__,
                 "message": "数据安全插件配置页面不可用，请修复应用安装包。"})
        return UnavailableSecurityPanel(value, parent, type(exc).__name__)
