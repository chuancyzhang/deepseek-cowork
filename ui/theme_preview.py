"""Local presentation samples and the always-available theme picker."""

import qtawesome as qta
from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget, QToolButton, QMenu, QPushButton

from core.theme import DesignTokens, bind_theme, default_design_tokens
from core.theme_presets import BUILTIN_THEMES, builtin_theme
from core.theme_service import resolve_theme
from ui.primitives import product_button_style, ProductMessageBox
from ui.theme_workspace import WorkspaceSceneCanvas


class ThemePreviewSample(QWidget):
    """A non-interactive workspace sample; never mutates application tokens."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ThemePreviewSample")
        self.setAccessibleName("主题效果示例")
        self.setMinimumHeight(210)
        self.setMinimumWidth(220)
        self.resolved = resolve_theme(builtin_theme("default"), default_design_tokens())
        self.scene = WorkspaceSceneCanvas(self)
        self.ink = _PreviewInk(self)

    def set_theme(self, resolved):
        self.resolved = resolved
        self.scene.set_scene(resolved.get("workspace_scene"), resolved.get("assets"),
                             resolved.get("_asset_bytes"), revision=str(resolved.get("id", "")))
        self.ink.raise_()
        self.update()
        self.ink.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.ink.setGeometry(self.rect())

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(self.resolved["tokens"]["bg_chat"]))


class _PreviewInk(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, _event):
        resolved = self.parentWidget().resolved
        t = resolved["tokens"]
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        width, height = self.width(), self.height()
        sidebar = 100 if width > 420 else 68
        p.fillRect(QRectF(0, 0, sidebar, height), QColor(t["bg_sidebar"]))
        p.setPen(QPen(QColor(t["border_subtle"]), 1))
        p.drawRect(QRectF(0.5, 0.5, width - 1, height - 1))
        p.drawLine(sidebar, 0, sidebar, height)
        font = QFont(resolved["font_family"])
        font.setPixelSize(min(17, max(11, t["font_size_body"])))
        p.setFont(font)
        p.setPen(QColor(t["sidebar_text"]))
        p.drawText(QRectF(12, 16, sidebar - 16, 24), "工作台")
        p.fillRect(QRectF(8, 54, sidebar - 16, 28), QColor(t["primary_soft"]))
        p.drawText(QRectF(16, 58, sidebar - 20, 24), "对话")
        p.drawText(QRectF(16, 98, sidebar - 20, 24), "资料")
        x, content_width = sidebar + 20, width - sidebar - 40
        p.setPen(QColor(t["chat_text"]))
        font.setBold(True)
        p.setFont(font)
        p.drawText(QRectF(x, 16, content_width, 30), "让思路更清晰")
        font.setBold(False)
        p.setFont(font)
        p.setPen(QColor(t["chat_text_muted"]))
        p.drawText(QRectF(x, 55, content_width, 50), Qt.TextWordWrap,
                   "从一个问题开始，整理想法、阅读材料，完成手头的工作。")
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(t["bg_user_bubble"]))
        p.drawRoundedRect(QRectF(x, 116, content_width, 32), 6, 6)
        p.setPen(QColor(t["chat_text"]))
        p.drawText(QRectF(x + 12, 121, content_width - 24, 24), "帮我理清今天的重点")
        p.setBrush(QColor(t["composer_bg"]))
        p.setPen(QColor(t["composer_border"]))
        p.drawRoundedRect(QRectF(x, height - 46, content_width, 32), 6, 6)
        p.setPen(QColor(t["composer_text_muted"]))
        p.drawText(QRectF(x + 10, height - 41, content_width - 50, 24), "继续描述你的想法…")
        p.setBrush(QColor(t["primary"]))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(width - 48, height - 40, 22, 22), 5, 5)


class ThemeTile(QPushButton):
    def __init__(self, theme_id, parent=None):
        super().__init__(BUILTIN_THEMES[theme_id][0], parent)
        self.theme_id = theme_id
        self.dark = False
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumSize(104, 94)
        self.setToolTip(BUILTIN_THEMES[theme_id][1])
        self.setAccessibleName(self.text() + "主题")

    def paintEvent(self, _event):
        t = resolve_theme(builtin_theme(self.theme_id, dark=self.dark), default_design_tokens())["tokens"]
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(DesignTokens.bg_main))
        p.setPen(QPen(QColor(DesignTokens.primary if self.isChecked() or self.hasFocus() else DesignTokens.border_subtle), 2 if self.isChecked() else 1))
        p.drawRoundedRect(QRectF(1, 1, self.width() - 2, self.height() - 2), 8, 8)
        p.fillRect(QRectF(10, 10, self.width() - 20, 42), QColor(t["bg_chat"]))
        p.fillRect(QRectF(10, 10, 26, 42), QColor(t["bg_sidebar"]))
        p.fillRect(QRectF(44, 18, self.width() - 62, 6), QColor(t["primary"]))
        p.fillRect(QRectF(44, 32, self.width() - 74, 5), QColor(t["border"]))
        p.setPen(QColor(DesignTokens.text_primary))
        font = self.font()
        font.setPixelSize(DesignTokens.font_size_body)
        p.setFont(font)
        p.drawText(QRectF(12, 64, self.width() - 20, 24), self.text() + (" · 当前" if self.isChecked() else ""))


class ThemeQuickPicker(QToolButton):
    manageRequested = Signal()

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.setObjectName("ThemeQuickPicker")
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setPopupMode(QToolButton.InstantPopup)
        self.setToolTip("切换工作台风格；可撤销上一次切换")
        self.setAccessibleName("快捷切换主题")
        self.menu = QMenu(self)
        self.setMenu(self.menu)
        self.menu.aboutToShow.connect(self._populate)
        self.setEnabled(callable(getattr(manager, "switch_theme", None)))
        if self.isEnabled():
            manager.themeChanged.connect(self.refresh_theme)
            manager.selectionChanged.connect(self.refresh_theme)
        bind_theme(self, self.refresh_theme, surface="global")
        self.refresh_theme()

    def refresh_theme(self, _resolved=None):
        current = getattr(self.manager, "current", None) or {}
        self.setText(current.get("name") or "主题")
        self.setIcon(qta.icon("fa5s.palette", color=DesignTokens.icon_secondary))
        self.setStyleSheet(product_button_style("ghost").replace("QPushButton", "QToolButton"))

    def _populate(self):
        self.menu.clear()
        try:
            snapshot = self.manager.repository.load()
            appearance = self.manager.repository.load_appearance()
            for key, (label, _detail) in BUILTIN_THEMES.items():
                action = self.menu.addAction(label)
                action.setCheckable(True)
                action.setChecked(snapshot.active_theme_id == key)
                action.triggered.connect(lambda _checked=False, value=key: self._switch(theme_id=value))
            if snapshot.themes:
                submenu = self.menu.addMenu("自定义主题")
                for theme in snapshot.themes:
                    action = submenu.addAction(theme["name"])
                    action.setCheckable(True)
                    action.setChecked(snapshot.active_theme_id == theme["id"])
                    action.triggered.connect(lambda _checked=False, key=theme["id"]: self._switch(theme_id=key))
            self.menu.addSeparator()
            modes = self.menu.addMenu("明暗模式")
            modes.setEnabled(snapshot.active_theme_id in BUILTIN_THEMES)
            modes.setToolTip("自定义主题使用自身配色")
            for key, label in (("system", "跟随系统"), ("light", "浅色"), ("dark", "深色")):
                action = modes.addAction(label)
                action.setCheckable(True)
                action.setChecked(appearance["mode"] == key)
                action.triggered.connect(lambda _checked=False, value=key: self._switch(mode=value))
            self.menu.addSeparator()
            undo = self.menu.addAction("撤销上次切换")
            undo.setEnabled(self.manager._undo_selection is not None)
            undo.triggered.connect(lambda: self._switch(undo=True))
            self.menu.addAction("外观与阅读设置…", self.manageRequested)
        except Exception as exc:
            self.menu.addAction("无法读取主题列表").setEnabled(False)
            ProductMessageBox.warning(self, "主题列表不可用", str(exc))

    def _switch(self, **kwargs):
        try:
            self.manager.switch_theme(**kwargs)
        except Exception as exc:
            ProductMessageBox.warning(self, "切换失败", f"原主题已保留。\n{exc}")
