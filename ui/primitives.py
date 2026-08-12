"""Small, scoped PySide6 primitives for the Cowork product language.

The helpers in this module deliberately avoid broad QWidget selectors. A
surface opts into a role through an object name or a dynamic property, which
prevents parent styling from leaking into labels and nested controls.
"""

import json
import re

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, Signal, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QMessageBox as QtMessageBox,
    QMenu,
    QPushButton,
    QButtonGroup,
    QScrollArea,
    QScrollBar,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyleOptionSlider,
    QToolButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.theme import DesignTokens, bind_theme


class ProductGrabScrollBar(QScrollBar):
    """A visually light scrollbar with a forgiving, explicit drag target."""

    def __init__(self, orientation=Qt.Vertical, parent=None):
        super().__init__(orientation, parent)
        self.setObjectName("ProductGrabScrollBar")
        self.setMouseTracking(True)
        self._product_dragging = False
        self._drag_anchor_coordinate = 0.0
        self._drag_anchor_position = 0
        self.refresh_theme()
        bind_theme(self, self.refresh_theme, surface="conversation")

    def refresh_theme(self, _resolved=None):
        visual_width = max(1, int(DesignTokens.chat_scrollbar_visual_width))
        hit_width = max(visual_width, int(DesignTokens.chat_scrollbar_hit_width))
        side_margin = max(0, (hit_width - visual_width) // 2)
        radius = max(1, visual_width // 2)
        self.setStyleSheet(
            f"QScrollBar#ProductGrabScrollBar:vertical {{ width: {hit_width}px; background: transparent; "
            "border: none; margin: 0; }}"
            f"QScrollBar#ProductGrabScrollBar::handle:vertical {{ background: {DesignTokens.scrollbar_thumb}; "
            f"min-height: 44px; border-radius: {radius}px; margin: 1px {side_margin}px; }}"
            f"QScrollBar#ProductGrabScrollBar::handle:vertical:hover {{ background: {DesignTokens.scrollbar_thumb_hover}; }}"
            f"QScrollBar#ProductGrabScrollBar::handle:vertical:pressed {{ background: {DesignTokens.primary}; }}"
            "QScrollBar#ProductGrabScrollBar::add-line:vertical, "
            "QScrollBar#ProductGrabScrollBar::sub-line:vertical { height: 0; background: transparent; }"
            "QScrollBar#ProductGrabScrollBar::add-page:vertical, "
            "QScrollBar#ProductGrabScrollBar::sub-page:vertical { background: transparent; }"
        )

    def _slider_rect(self):
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        return self.style().subControlRect(
            QStyle.CC_ScrollBar,
            option,
            QStyle.SC_ScrollBarSlider,
            self,
        )

    def _grab_rect(self):
        padding = max(0, int(DesignTokens.chat_scrollbar_grab_padding))
        rect = self._slider_rect()
        if self.orientation() == Qt.Vertical:
            rect.adjust(0, -padding, 0, padding)
        else:
            rect.adjust(-padding, 0, padding, 0)
        return rect.intersected(self.rect())

    def _event_coordinate(self, event):
        position = event.position()
        return position.y() if self.orientation() == Qt.Vertical else position.x()

    def _update_hover_cursor(self, point):
        if self._product_dragging:
            self.setCursor(Qt.ClosedHandCursor)
        elif self._grab_rect().contains(point):
            self.setCursor(Qt.OpenHandCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._grab_rect().contains(event.position().toPoint()):
            self._product_dragging = True
            self._drag_anchor_coordinate = self._event_coordinate(event)
            self._drag_anchor_position = self.sliderPosition()
            self.setSliderDown(True)
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self._product_dragging:
            self._update_hover_cursor(event.position().toPoint())
            super().mouseMoveEvent(event)
            return
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        groove = self.style().subControlRect(
            QStyle.CC_ScrollBar,
            option,
            QStyle.SC_ScrollBarGroove,
            self,
        )
        handle = self._slider_rect()
        if self.orientation() == Qt.Vertical:
            span = groove.height() - handle.height()
        else:
            span = groove.width() - handle.width()
        value_span = self.maximum() - self.minimum()
        if span > 0 and value_span > 0:
            delta = self._event_coordinate(event) - self._drag_anchor_coordinate
            if self.invertedAppearance():
                delta = -delta
            position = self._drag_anchor_position + int(round(delta * value_span / span))
            self.setSliderPosition(max(self.minimum(), min(self.maximum(), position)))
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._product_dragging and event.button() == Qt.LeftButton:
            self._product_dragging = False
            self.setSliderDown(False)
            self._update_hover_cursor(event.position().toPoint())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        if not self._product_dragging:
            self.setCursor(Qt.ArrowCursor)
        super().leaveEvent(event)


class _ProductCodeHighlighter(QSyntaxHighlighter):
    """Small dependency-free highlighter for diagnostic code surfaces."""

    def __init__(self, document, language="text"):
        super().__init__(document)
        self.language = str(language or "text").lower()
        self.rules = []
        keyword = QTextCharFormat()
        keyword.setForeground(QColor(DesignTokens.primary))
        keyword.setFontWeight(QFont.DemiBold)
        string = QTextCharFormat()
        string.setForeground(QColor(DesignTokens.success_text))
        number = QTextCharFormat()
        number.setForeground(QColor(DesignTokens.warning_text))
        comment = QTextCharFormat()
        comment.setForeground(QColor(DesignTokens.text_tertiary))
        if self.language == "python":
            words = (
                "and as assert async await break class continue def del elif else except False "
                "finally for from global if import in is lambda None nonlocal not or pass raise "
                "return True try while with yield"
            ).split()
            self.rules.append((re.compile(r"\b(?:" + "|".join(words) + r")\b"), keyword))
            self.rules.extend([(re.compile(r"(['\"]).*?\1"), string), (re.compile(r"\b\d+(?:\.\d+)?\b"), number), (re.compile(r"#.*$"), comment)])
        elif self.language in {"shell", "bash"}:
            self.rules.extend([(re.compile(r"\b(?:if|then|else|fi|for|do|done|case|esac|function|while|in)\b"), keyword), (re.compile(r"(['\"]).*?\1"), string), (re.compile(r"#.*$"), comment)])
        elif self.language == "json":
            self.rules.extend([(re.compile(r'"(?:\\.|[^"\\])*"(?=\s*:)'), keyword), (re.compile(r'"(?:\\.|[^"\\])*"'), string), (re.compile(r"\b(?:true|false|null|-?\d+(?:\.\d+)?)\b"), number)])

    def highlightBlock(self, text):
        for pattern, fmt in self.rules:
            for match in pattern.finditer(text):
                self.setFormat(match.start(), match.end() - match.start(), fmt)


class ProductCodeViewer(QPlainTextEdit):
    """Read-only code viewer with line numbers and lightweight highlighting."""

    def __init__(self, language="text", parent=None):
        super().__init__(parent)
        self._line_number_area = QWidget(self)
        self._line_number_area.paintEvent = self._paint_line_numbers
        self._header = QFrame(self)
        self._header.setObjectName("ProductCodeViewerHeader")
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(10, 0, 6, 0)
        header_layout.setSpacing(6)
        self.language_label = QLabel("TEXT")
        self.language_label.setObjectName("ProductCodeLanguage")
        header_layout.addWidget(self.language_label)
        header_layout.addStretch()
        self.copy_button = QToolButton()
        self.copy_button.setText("复制")
        self.copy_button.setToolTip("复制代码")
        self.copy_button.setCursor(Qt.PointingHandCursor)
        self.copy_button.clicked.connect(self._copy_all)
        header_layout.addWidget(self.copy_button)
        self._language = "text"
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        initial_font = QFont("Consolas")
        initial_font.setPixelSize(DesignTokens.font_size_body)
        self.setFont(initial_font)
        self.setStyleSheet(
            product_code_style("QPlainTextEdit")
            + f"QFrame#ProductCodeViewerHeader {{ background: {DesignTokens.bg_secondary}; border: none; "
              f"border-bottom: 1px solid {DesignTokens.border_subtle}; }}"
              f"QLabel#ProductCodeLanguage {{ color: {DesignTokens.text_tertiary}; font-size: 10px; font-weight: 700; }}"
              f"QToolButton {{ color: {DesignTokens.text_secondary}; background: transparent; border: none; "
              f"border-radius: 5px; padding: 3px 7px; font-size: 11px; }}"
              f"QToolButton:hover {{ color: {DesignTokens.primary}; background: {DesignTokens.primary_soft}; }}"
        )
        self.blockCountChanged.connect(self._update_line_number_width)
        self.updateRequest.connect(self._update_line_number_area)
        self._highlighter = None
        self.set_language(language)
        self._update_line_number_width()
        bind_theme(self, self.refresh_theme, surface="controls")

    def set_language(self, language):
        self._language = str(language or "text").lower()
        if hasattr(self, "language_label"):
            self.language_label.setText({"shell": "BASH"}.get(self._language, self._language.upper()))
        if self._highlighter is not None:
            self._highlighter.setDocument(None)
        self._highlighter = _ProductCodeHighlighter(self.document(), self._language)

    def refresh_theme(self, _resolved=None):
        app = QApplication.instance()
        mono_family = (
            str(app.property("themeMonoFontFamily") or "Consolas")
            if app is not None
            else "Consolas"
        )
        mono_font = QFont(mono_family)
        mono_font.setPixelSize(DesignTokens.font_size_body)
        self.setFont(mono_font)
        self.setStyleSheet(
            product_code_style("QPlainTextEdit")
            + f"QFrame#ProductCodeViewerHeader {{ background: {DesignTokens.bg_secondary}; border: none; "
              f"border-bottom: 1px solid {DesignTokens.border_subtle}; }}"
              f"QLabel#ProductCodeLanguage {{ color: {DesignTokens.text_tertiary}; font-size: 10px; font-weight: 700; }}"
              f"QToolButton {{ color: {DesignTokens.text_secondary}; background: transparent; border: none; "
              f"border-radius: 5px; padding: 3px 7px; font-size: 11px; }}"
              f"QToolButton:hover {{ color: {DesignTokens.primary}; background: {DesignTokens.primary_soft}; }}"
        )
        self.set_language(self._language)
        self.viewport().update()

    def set_code(self, text, language=None):
        if language is not None:
            self.set_language(language)
        self.setPlainText(str(text or ""))

    def _line_number_width(self):
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 12 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_line_number_width(self, *_):
        self.setViewportMargins(self._line_number_width(), 30, 0, 0)

    def _update_line_number_area(self, rect, dy):
        if dy:
            self._line_number_area.scroll(0, dy)
        else:
            self._line_number_area.update(0, rect.y(), self._line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_line_number_width()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        rect = self.contentsRect()
        self._header.setGeometry(QRect(rect.left(), rect.top(), rect.width(), 30))
        self._header.raise_()
        self._line_number_area.setGeometry(QRect(rect.left(), rect.top() + 30, self._line_number_width(), max(0, rect.height() - 30)))

    def _copy_all(self):
        QApplication.clipboard().setText(self.toPlainText())
        self.copy_button.setText("已复制")
        QTimer.singleShot(1200, lambda: self.copy_button.setText("复制"))

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {DesignTokens.bg_main}; border: 1px solid {DesignTokens.border}; "
            f"border-radius: {DesignTokens.radius_md}px; padding: 6px; }}"
            f"QMenu::item {{ min-height: {DesignTokens.control_height_sm}px; padding: 4px 24px 4px 10px; "
            f"border-radius: {DesignTokens.radius_sm}px; color: {DesignTokens.text_primary}; }}"
            f"QMenu::item:selected {{ background: {DesignTokens.primary_soft}; }}"
            f"QMenu::item:disabled {{ color: {DesignTokens.text_disabled}; }}"
        )
        copy_action = QAction("复制", self)
        copy_action.setShortcut("Ctrl+C")
        copy_action.setEnabled(self.textCursor().hasSelection())
        copy_action.triggered.connect(self.copy)
        menu.addAction(copy_action)
        copy_all = QAction("复制全部", self)
        copy_all.setEnabled(bool(self.toPlainText().strip()))
        copy_all.triggered.connect(self._copy_all)
        menu.addAction(copy_all)
        menu.addSeparator()
        select_all = QAction("全选", self)
        select_all.setShortcut("Ctrl+A")
        select_all.triggered.connect(self.selectAll)
        menu.addAction(select_all)
        menu.exec(event.globalPos())

    def _paint_line_numbers(self, event):
        painter = QPainter(self._line_number_area)
        painter.fillRect(event.rect(), QColor(DesignTokens.bg_secondary))
        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())
        painter.setPen(QColor(DesignTokens.text_tertiary))
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(0, top, self._line_number_area.width() - 6, self.fontMetrics().height(), Qt.AlignRight, str(number + 1))
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            number += 1


class ProductResultViewer(ProductCodeViewer):
    """Structured diagnostic result surface with explicit format handling."""

    def set_result(self, value):
        if isinstance(value, (dict, list)):
            self.set_code(json.dumps(value, ensure_ascii=False, indent=2), "json")
            return "json"
        text = str(value or "")
        stripped = text.strip()
        if stripped.startswith(("{", "[")):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as exc:
                self.set_code(f"无法解析为 JSON：{exc}\n\n{text}", "text")
                return "invalid_json"
            self.set_code(json.dumps(parsed, ensure_ascii=False, indent=2), "json")
            return "json"
        if "Traceback (most recent call last)" in text:
            language = "python"
        elif re.search(r"(^|\n)(stdout|stderr|exit code|command):", text, re.IGNORECASE):
            language = "shell"
        else:
            language = "text"
        self.set_code(text, language)
        return language


class ProductPopover(QFrame):
    """App-owned in-window popover with deterministic focus and edge clamping."""

    closed = Signal()

    def __init__(self, parent=None, width=360):
        super().__init__(parent)
        self.setObjectName("ProductPopover")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumWidth(int(width))
        self.setMaximumWidth(int(width))
        self._anchor = None
        self._outside_filter_installed = False
        self.setStyleSheet(
            f"QFrame#ProductPopover {{ background: {DesignTokens.bg_main}; "
            f"border: 1px solid {DesignTokens.border_subtle}; border-radius: 8px; }}"
        )
        self.hide()
        bind_theme(self, self.refresh_theme, surface="feedback")

    def refresh_theme(self, _resolved=None):
        self.setStyleSheet(
            f"QFrame#ProductPopover {{ background: {DesignTokens.overlay_bg}; "
            f"color: {DesignTokens.overlay_text}; "
            f"border: 1px solid {DesignTokens.overlay_border}; "
            f"border-radius: {DesignTokens.overlay_radius}px; }}"
        )

    def show_for(self, anchor, *, align_right=False, prefer_above=True, gap=6):
        if anchor is None or not anchor.isVisible():
            return False
        host = self.parentWidget()
        if host is None:
            raise RuntimeError("ProductPopover requires an in-window parent widget.")
        for popover in host.findChildren(ProductPopover):
            if popover is not self and popover.isVisible():
                popover.close()
        self._anchor = anchor
        self.adjustSize()
        available = host.rect().adjusted(8, 8, -8, -8)
        anchor_top_left = anchor.mapTo(host, QPoint(0, 0))
        anchor_bottom_left = anchor.mapTo(host, QPoint(0, anchor.height()))
        x = anchor_top_left.x()
        if align_right:
            x = anchor_top_left.x() + anchor.width() - self.width()
        above_y = anchor_top_left.y() - self.height() - int(gap)
        below_y = anchor_bottom_left.y() + int(gap)
        if prefer_above and above_y >= available.top():
            y = above_y
        elif below_y + self.height() <= available.bottom():
            y = below_y
        else:
            y = max(available.top(), min(above_y, available.bottom() - self.height()))
        x = max(available.left(), min(x, available.right() - self.width()))
        self.move(x, y)
        self.show()
        self.raise_()
        if not self._outside_filter_installed:
            QApplication.instance().installEventFilter(self)
            self._outside_filter_installed = True
        self.setFocus(Qt.OtherFocusReason)
        return True

    def eventFilter(self, obj, event):
        if self.isVisible() and event.type() == QEvent.MouseButtonPress:
            anchor = self._anchor
            global_position = None
            position_getter = getattr(event, "globalPosition", None)
            if callable(position_getter):
                try:
                    global_position = position_getter().toPoint()
                except (AttributeError, RuntimeError, TypeError):
                    global_position = None

            if global_position is not None:
                try:
                    inside_popover = self.rect().contains(self.mapFromGlobal(global_position))
                    inside_anchor = bool(
                        anchor
                        and anchor.isVisible()
                        and anchor.rect().contains(anchor.mapFromGlobal(global_position))
                    )
                except RuntimeError:
                    inside_popover = False
                    inside_anchor = False
            else:
                widget = obj if isinstance(obj, QWidget) else None
                inside_popover = widget is self or bool(widget and self.isAncestorOf(widget))
                inside_anchor = widget is anchor or bool(widget and anchor and anchor.isAncestorOf(widget))
            if not inside_popover and not inside_anchor:
                self.close()
        return super().eventFilter(obj, event)

    def hideEvent(self, event):
        if self._outside_filter_installed and QApplication.instance() is not None:
            QApplication.instance().removeEventFilter(self)
            self._outside_filter_installed = False
        self._anchor = None
        super().hideEvent(event)
        self.closed.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)


class ProductActionRow(QFrame):
    """Compact Linear-style action row with an icon, title, and optional detail."""

    clicked = Signal()

    def __init__(self, title, detail="", icon=None, parent=None):
        super().__init__(parent)
        self.setObjectName("ProductActionRow")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumHeight(40 if not detail else 52)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(10)
        self.icon_label = QLabel(self)
        self.icon_label.setFixedSize(18, 18)
        self.icon_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.icon_label)
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(1)
        self.title_label = QLabel(str(title or ""), self)
        self.title_label.setObjectName("ProductActionRowTitle")
        self.detail_label = QLabel(str(detail or ""), self)
        self.detail_label.setObjectName("ProductActionRowDetail")
        self.detail_label.setVisible(bool(detail))
        text_layout.addWidget(self.title_label)
        text_layout.addWidget(self.detail_label)
        layout.addLayout(text_layout, 1)
        self._icon = QIcon()
        self.setIcon(icon)
        self._apply_style()
        bind_theme(self, self.refresh_theme, surface="feedback")

    def _apply_style(self):
        self.setStyleSheet(
            f"QFrame#ProductActionRow {{ background: transparent; border: none; border-radius: 6px; }}"
            f"QFrame#ProductActionRow:hover, QFrame#ProductActionRow:focus {{ background: {DesignTokens.bg_hover}; }}"
            f"QLabel#ProductActionRowTitle {{ color: {DesignTokens.text_primary}; font-size: 12px; font-weight: 600; }}"
            f"QLabel#ProductActionRowDetail {{ color: {DesignTokens.text_tertiary}; font-size: 11px; }}"
        )

    def refresh_theme(self, _resolved=None):
        self._apply_style()
        self.setEnabled(self.isEnabled())

    def setIcon(self, icon):
        self._icon = icon if isinstance(icon, QIcon) else QIcon()
        self.icon_label.setPixmap(self._icon.pixmap(16, 16) if not self._icon.isNull() else QIcon().pixmap(16, 16))

    def setTitle(self, title):
        self.title_label.setText(str(title or ""))

    def setDetail(self, detail):
        self.detail_label.setText(str(detail or ""))
        self.detail_label.setVisible(bool(detail))
        self.setMinimumHeight(40 if not detail else 52)

    def setEnabled(self, enabled):
        super().setEnabled(enabled)
        opacity_color = DesignTokens.text_primary if enabled else DesignTokens.text_disabled
        self.title_label.setStyleSheet(f"color: {opacity_color};")
        self.setCursor(Qt.PointingHandCursor if enabled else Qt.ArrowCursor)

    def mouseReleaseEvent(self, event):
        if self.isEnabled() and event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if self.isEnabled() and event.key() in {Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space}:
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class SidebarInlineNameEditor(QLineEdit):
    """Inline rename field with deterministic commit/cancel behavior."""

    commitRequested = Signal(str)
    cancelRequested = Signal()

    def __init__(self, text="", parent=None):
        super().__init__(str(text or ""), parent)
        self._resolved = False
        self.setMaxLength(80)
        self.setFixedHeight(28)
        self.setStyleSheet(product_field_style())
        self.selectAll()
        bind_theme(self, self.refresh_theme, surface="left_sidebar")

    def refresh_theme(self, _resolved=None):
        self.setStyleSheet(product_field_style())

    def keyPressEvent(self, event):
        if event.key() in {Qt.Key_Return, Qt.Key_Enter}:
            self._commit()
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            self._resolved = True
            self.cancelRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        if not self._resolved:
            self._commit()

    def _commit(self):
        if self._resolved:
            return
        self._resolved = True
        self.commitRequested.emit(self.text().strip())


def display_run_phase(value):
    """Return concise product copy for an internal execution phase."""
    raw = str(value or "").strip()
    key = raw.casefold().replace("_", " ").replace("-", " ")
    labels = {
        "preparing": "准备中",
        "analyzing": "思考中",
        "clarifying": "等待输入",
        "executing": "运行中",
        "delegating": "运行中",
        "wrapping up": "收尾中",
        "finalizing": "收尾中",
        "interrupted": "已停止",
        "clarified": "运行中",
        "awaiting input": "等待输入",
        "waiting input": "等待输入",
        "running": "运行中",
        "thinking": "思考中",
        "paused": "已暂停",
        "stopped": "已停止",
        "completed": "已完成",
        "complete": "已完成",
        "failed": "失败",
        "error": "失败",
        "idle": "待开始",
    }
    return labels.get(key, raw or "待开始")


class ProductMessageDialog(QDialog):
    """Small Linear-style message/confirmation surface with explicit results."""

    TONES = {
        "information": ("提示", DesignTokens.info_icon, DesignTokens.toast_tint_info),
        "success": ("完成", DesignTokens.success_icon, DesignTokens.toast_tint_success),
        "warning": ("请注意", DesignTokens.warning_icon, DesignTokens.toast_tint_warning),
        "error": ("发生错误", DesignTokens.error_icon, DesignTokens.toast_tint_error),
        "confirm": ("请确认", DesignTokens.info_icon, DesignTokens.toast_tint_info),
        "destructive": ("请确认", DesignTokens.error_icon, DesignTokens.toast_tint_error),
    }

    def __init__(self, title, message, tone="information", buttons=None, details="", parent=None):
        super().__init__(parent)
        self.result_value = None
        self.setObjectName("ProductMessageDialog")
        self.setWindowTitle(str(title or "提示"))
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setMaximumWidth(620)
        self.setStyleSheet(
            f"""
            QDialog#ProductMessageDialog {{ background: {DesignTokens.bg_app}; }}
            QDialog#ProductMessageDialog QLabel {{ background: transparent; border: none; }}
            QFrame#MessageIcon {{ border: none; border-radius: 8px; }}
            QFrame#MessageActions {{ border: none; border-top: 1px solid {DesignTokens.separator}; }}
            """
            + product_field_style()
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(14)

        tone_title, icon_color, icon_bg = self.TONES.get(tone, self.TONES["information"])
        header = QHBoxLayout()
        header.setSpacing(10)
        icon = QLabel("!")
        icon.setObjectName("MessageIcon")
        icon.setAlignment(Qt.AlignCenter)
        icon.setFixedSize(28, 28)
        icon.setStyleSheet(
            f"background:{icon_bg};color:{icon_color};border:none;border-radius:8px;"
            "font-size:14px;font-weight:700;"
        )
        header.addWidget(icon, 0, Qt.AlignTop)
        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        title_label = QLabel(str(title or tone_title))
        title_label.setStyleSheet(
            f"color:{DesignTokens.text_primary};font-size:15px;font-weight:650;"
        )
        body = QLabel(str(message or ""))
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.setStyleSheet(f"color:{DesignTokens.text_secondary};font-size:13px;")
        text_col.addWidget(title_label)
        text_col.addWidget(body)
        header.addLayout(text_col, 1)
        layout.addLayout(header)

        if details:
            detail = QLabel(str(details))
            detail.setWordWrap(True)
            detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
            detail.setStyleSheet(
                f"background:{DesignTokens.bg_code};color:{DesignTokens.text_secondary};"
                f"border:1px solid {DesignTokens.border_subtle};border-radius:6px;padding:8px;font-size:12px;"
            )
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setMaximumHeight(180)
            scroll.setWidget(detail)
            layout.addWidget(scroll)

        action_frame = QFrame()
        action_frame.setObjectName("MessageActions")
        actions = QHBoxLayout(action_frame)
        actions.setContentsMargins(0, 12, 0, 0)
        actions.setSpacing(8)
        actions.addStretch()
        specs = list(buttons or [("知道了", QtMessageBox.Ok, "primary", True)])
        for label, result, kind, is_default in specs:
            button = QPushButton(str(label))
            button.setStyleSheet(product_button_style("danger" if kind == "danger" else kind))
            button.clicked.connect(lambda _checked=False, value=result: self._finish(value))
            if is_default:
                button.setDefault(True)
                button.setFocus()
            actions.addWidget(button)
        layout.addWidget(action_frame)

    def _finish(self, value):
        self.result_value = value
        self.accept()

    def exec_result(self, fallback=None):
        self.exec()
        return self.result_value if self.result_value is not None else fallback


def _standard_button_specs(buttons, default_button, destructive=False):
    labels = {
        QtMessageBox.Yes: "继续",
        QtMessageBox.No: "取消",
        QtMessageBox.Ok: "知道了",
        QtMessageBox.Cancel: "取消",
        QtMessageBox.Save: "保存",
        QtMessageBox.Discard: "不保存",
        QtMessageBox.Retry: "重试",
        QtMessageBox.Close: "关闭",
    }
    order = [
        QtMessageBox.Save,
        QtMessageBox.Yes,
        QtMessageBox.Retry,
        QtMessageBox.Ok,
        QtMessageBox.Discard,
        QtMessageBox.No,
        QtMessageBox.Cancel,
        QtMessageBox.Close,
    ]
    specs = []
    for value in order:
        if buttons & value:
            primary = value in {QtMessageBox.Yes, QtMessageBox.Save, QtMessageBox.Retry, QtMessageBox.Ok}
            kind = "danger" if destructive and primary else ("primary" if primary else "secondary")
            specs.append((labels[value], value, kind, value == default_button))
    return specs or [("知道了", QtMessageBox.Ok, "primary", True)]


def _show_parent_toast(parent, text, tone="info"):
    current = parent
    visited = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if hasattr(current, "add_system_toast"):
            current.add_system_toast(str(text or ""), tone)
            return True
        linked_main = getattr(current, "_main", None)
        if linked_main is not None and hasattr(linked_main, "add_system_toast"):
            linked_main.add_system_toast(str(text or ""), tone)
            return True
        current = current.parentWidget() if hasattr(current, "parentWidget") else None
    return False


class ProductMessageBox:
    """Compatibility facade used while business call sites migrate from QMessageBox."""

    for _name in (
        "Ok", "Yes", "No", "Cancel", "Save", "Discard", "Retry", "Close",
        "YesRole", "NoRole", "AcceptRole", "RejectRole", "DestructiveRole",
        "Information", "Warning", "Critical", "Question",
    ):
        locals()[_name] = getattr(QtMessageBox, _name)

    @staticmethod
    def information(parent, title, text, buttons=QtMessageBox.Ok, defaultButton=QtMessageBox.Ok):
        if buttons == QtMessageBox.Ok:
            tone = "success" if any(token in str(title or "") for token in ("成功", "完成", "已保存", "通过")) else "info"
            if _show_parent_toast(parent, text, tone):
                return QtMessageBox.Ok
        specs = _standard_button_specs(buttons, defaultButton)
        return ProductMessageDialog(title, text, "information", specs, parent=parent).exec_result(QtMessageBox.Ok)

    @staticmethod
    def warning(parent, title, text, buttons=QtMessageBox.Ok, defaultButton=QtMessageBox.Ok):
        specs = _standard_button_specs(buttons, defaultButton)
        return ProductMessageDialog(title, text, "warning", specs, parent=parent).exec_result(QtMessageBox.Ok)

    @staticmethod
    def critical(parent, title, text, buttons=QtMessageBox.Ok, defaultButton=QtMessageBox.Ok):
        specs = _standard_button_specs(buttons, defaultButton)
        return ProductMessageDialog(title, text, "error", specs, parent=parent).exec_result(QtMessageBox.Ok)

    @staticmethod
    def question(parent, title, text, buttons=QtMessageBox.Yes | QtMessageBox.No, defaultButton=QtMessageBox.No):
        destructive = any(token in str(title or "") for token in ("删除", "卸载", "清空", "放弃"))
        specs = _standard_button_specs(buttons, defaultButton, destructive=destructive)
        return ProductMessageDialog(
            title, text, "destructive" if destructive else "confirm", specs, parent=parent
        ).exec_result(defaultButton)


class ProductInputDialog:
    @staticmethod
    def getText(parent, title, label, mode=QLineEdit.Normal, text="", *args, **kwargs):
        dialog = QDialog(parent)
        dialog.setObjectName("ProductInputDialog")
        dialog.setWindowTitle(str(title or "输入"))
        dialog.setModal(True)
        dialog.setMinimumWidth(420)
        dialog.setStyleSheet(
            f"QDialog#ProductInputDialog{{background:{DesignTokens.bg_app};}}"
            + product_field_style()
        )
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        heading = QLabel(str(title or "输入"))
        heading.setStyleSheet(f"color:{DesignTokens.text_primary};font-size:15px;font-weight:650;")
        prompt = QLabel(str(label or ""))
        prompt.setWordWrap(True)
        prompt.setStyleSheet(f"color:{DesignTokens.text_secondary};font-size:13px;")
        field = QLineEdit(str(text or ""))
        field.setEchoMode(mode)
        actions = QHBoxLayout()
        actions.addStretch()
        cancel = QPushButton("取消")
        cancel.setStyleSheet(product_button_style("secondary"))
        submit = QPushButton("确定")
        submit.setStyleSheet(product_button_style("primary"))
        submit.setDefault(True)
        cancel.clicked.connect(dialog.reject)
        submit.clicked.connect(dialog.accept)
        field.returnPressed.connect(dialog.accept)
        actions.addWidget(cancel)
        actions.addWidget(submit)
        layout.addWidget(heading)
        layout.addWidget(prompt)
        layout.addWidget(field)
        layout.addLayout(actions)
        field.selectAll()
        field.setFocus()
        accepted = dialog.exec() == QDialog.Accepted
        return field.text(), accepted


class ProductTooltipController(QObject):
    """Render tooltip text as a child surface instead of a native Windows popup."""

    MAX_WIDTH = 360
    OFFSET = 10
    MARGIN = 8

    def __init__(self, app=None):
        app = app or QApplication.instance()
        if app is None:
            raise RuntimeError("ProductTooltipController requires a QApplication")
        super().__init__(app)
        self.app = app
        self.bubble = None
        self._hiding = False
        app.installEventFilter(self)

    def _item_view_for(self, widget):
        current = widget
        while current is not None:
            if isinstance(current, QAbstractItemView):
                return current
            current = current.parentWidget() if hasattr(current, "parentWidget") else None
        return None

    def _tooltip_text(self, widget, event):
        text = str(widget.toolTip() or "").strip() if hasattr(widget, "toolTip") else ""
        if text:
            return text
        view = self._item_view_for(widget)
        if view is None or not hasattr(event, "pos"):
            return ""
        position = event.pos()
        if widget is not view.viewport():
            position = view.viewport().mapFrom(widget, position)
        index = view.indexAt(position)
        if not index.isValid():
            return ""
        return str(index.data(Qt.ToolTipRole) or "").strip()

    def _ensure_bubble(self, window):
        if self.bubble is not None:
            try:
                if self.bubble.parentWidget() is window:
                    return self.bubble
            except RuntimeError:
                self.bubble = None
        if self.bubble is not None:
            self.bubble.deleteLater()
        self.bubble = QLabel(window)
        self.bubble.setObjectName("ProductTooltip")
        self.bubble.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.bubble.setTextFormat(Qt.PlainText)
        self.bubble.setWordWrap(True)
        self.bubble.setMaximumWidth(self.MAX_WIDTH)
        self.bubble.setStyleSheet(
            f"QLabel#ProductTooltip {{ background: {DesignTokens.bg_main}; "
            f"color: {DesignTokens.text_primary}; border: 1px solid {DesignTokens.border_subtle}; "
            f"border-radius: {DesignTokens.radius_sm}px; padding: 6px 8px; "
            f"font-size: {DesignTokens.font_size_meta}px; }}"
        )
        self.bubble.hide()
        return self.bubble

    def show_for_event(self, widget, event, text):
        window = widget.window()
        if window is None or not window.isVisible():
            return
        bubble = self._ensure_bubble(window)
        bubble.setText(text)
        bubble.adjustSize()
        global_pos = event.globalPos() if hasattr(event, "globalPos") else widget.mapToGlobal(widget.rect().bottomLeft())
        position = window.mapFromGlobal(global_pos)
        x = position.x() + self.OFFSET
        y = position.y() + self.OFFSET
        x = max(self.MARGIN, min(x, window.width() - bubble.width() - self.MARGIN))
        y = max(self.MARGIN, min(y, window.height() - bubble.height() - self.MARGIN))
        bubble.move(x, y)
        bubble.raise_()
        bubble.show()

    def hide(self):
        if self.bubble is None or self._hiding:
            return
        self._hiding = True
        try:
            if self.bubble.isVisible():
                self.bubble.hide()
        except RuntimeError:
            self.bubble = None
        finally:
            self._hiding = False

    def dispose(self):
        self.app.removeEventFilter(self)
        self.hide()
        self.bubble = None

    def eventFilter(self, obj, event):
        event_type = event.type()
        if event_type == QEvent.ToolTip and isinstance(obj, QWidget):
            text = self._tooltip_text(obj, event)
            if text:
                self.show_for_event(obj, event, text)
                event.accept()
                return True
        elif event_type in {
            QEvent.Leave,
            QEvent.MouseButtonPress,
            QEvent.Wheel,
            QEvent.KeyPress,
            QEvent.Hide,
            QEvent.FocusOut,
        }:
            self.hide()
        return super().eventFilter(obj, event)


def product_button_style(kind="secondary", radius=None):
    radius = DesignTokens.radius_md if radius is None else int(radius)
    palette = {
        "primary": (
            DesignTokens.primary,
            DesignTokens.text_inverse,
            DesignTokens.primary,
            DesignTokens.primary_hover,
            DesignTokens.primary_pressed,
        ),
        "danger": (
            DesignTokens.error_bg,
            DesignTokens.error_text,
            DesignTokens.error_border,
            DesignTokens.error_hover_bg,
            DesignTokens.error_pressed_bg,
        ),
        "ghost": (
            "transparent",
            DesignTokens.text_secondary,
            "transparent",
            DesignTokens.bg_hover,
            DesignTokens.bg_pressed,
        ),
        "secondary": (
            DesignTokens.bg_main,
            DesignTokens.text_primary,
            DesignTokens.border,
            DesignTokens.bg_hover,
            DesignTokens.bg_pressed,
        ),
    }
    bg, fg, border, hover, pressed = palette.get(kind, palette["secondary"])
    return f"""
        QPushButton {{
            min-height: {DesignTokens.control_height}px;
            background: {bg}; color: {fg}; border: 1px solid {border};
            border-radius: {radius}px; padding: 0 12px; font-weight: 600;
        }}
        QPushButton:hover {{ background: {hover}; }}
        QPushButton:pressed {{ background: {pressed}; }}
        QPushButton:focus {{ border: {DesignTokens.focus_ring_width}px solid {DesignTokens.primary_focus}; }}
        QPushButton:disabled {{
            background: {DesignTokens.bg_disabled}; color: {DesignTokens.text_disabled};
            border-color: {DesignTokens.border_subtle};
        }}
    """


def product_surface_style(kind="panel", radius=None):
    radius = DesignTokens.radius_md if radius is None else int(radius)
    backgrounds = {
        "canvas": DesignTokens.bg_app,
        "panel": DesignTokens.bg_main,
        "subtle": DesignTokens.bg_secondary,
        "selected": DesignTokens.primary_soft,
        "warning": DesignTokens.warning_panel_bg,
    }
    bg = backgrounds.get(kind, backgrounds["panel"])
    return (
        f'QFrame[productSurface="{kind}"] {{ background: {bg}; border: none; '
        f"border-radius: {radius}px; }}"
    )


def product_segmented_style():
    return f"""
        QPushButton {{
            min-height: {DesignTokens.control_height_sm}px;
            background: transparent; color: {DesignTokens.text_secondary};
            border: none; border-radius: {DesignTokens.radius_sm}px;
            padding: 0 10px; font-weight: 600;
        }}
        QPushButton:hover {{ background: {DesignTokens.bg_hover}; color: {DesignTokens.text_primary}; }}
        QPushButton:pressed {{ background: {DesignTokens.bg_pressed}; }}
        QPushButton:checked {{ background: {DesignTokens.bg_main}; color: {DesignTokens.primary}; }}
        QPushButton:focus {{ border: 1px solid {DesignTokens.primary_focus}; }}
        QPushButton:disabled {{ color: {DesignTokens.text_disabled}; background: transparent; }}
    """


def product_field_style():
    return f"""
        QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDateTimeEdit {{
            min-height: {DesignTokens.control_height}px;
            background: {DesignTokens.bg_main}; color: {DesignTokens.text_primary};
            border: 1px solid {DesignTokens.border}; border-radius: {DesignTokens.radius_md}px;
            padding: 0 9px; selection-background-color: {DesignTokens.selection_bg};
            selection-color: {DesignTokens.selection_text};
        }}
        QTextEdit, QPlainTextEdit {{ padding: 8px 10px; }}
        QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover, QComboBox:hover {{
            border-color: {DesignTokens.border_strong};
        }}
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
        QSpinBox:focus, QDateTimeEdit:focus {{
            border: {DesignTokens.focus_ring_width}px solid {DesignTokens.primary_focus};
        }}
        QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled,
        QSpinBox:disabled, QDateTimeEdit:disabled {{
            background: {DesignTokens.bg_disabled}; color: {DesignTokens.text_disabled};
        }}
        QLineEdit:read-only, QTextEdit:read-only, QPlainTextEdit:read-only {{
            background: {DesignTokens.bg_code}; color: {DesignTokens.text_secondary};
        }}
    """


def product_code_style(selector="QPlainTextEdit, QTextEdit"):
    selector = str(selector or "QPlainTextEdit, QTextEdit")
    focus_selectors = ", ".join(f"{part.strip()}:focus" for part in selector.split(","))
    app = QApplication.instance()
    mono_family = (
        str(app.property("themeMonoFontFamily") or "Consolas")
        if app is not None
        else "Consolas"
    ).replace("'", "")
    return f"""
        {selector} {{
            background: {DesignTokens.bg_code}; color: {DesignTokens.text_primary};
            border: 1px solid {DesignTokens.border_subtle}; border-radius: {DesignTokens.radius_md}px;
            padding: 10px; font-family: '{mono_family}', 'Cascadia Mono', 'Consolas', monospace;
            font-size: {DesignTokens.font_size_body}px;
            selection-background-color: {DesignTokens.selection_bg};
            selection-color: {DesignTokens.selection_text};
        }}
        {focus_selectors} {{ border-color: {DesignTokens.primary_focus}; }}
    """


def apply_product_dialog(dialog, object_name):
    dialog.setObjectName(object_name)
    dialog.setStyleSheet(
        f"""
        QDialog#{object_name} {{ background: {DesignTokens.bg_app}; }}
        QDialog#{object_name} QLabel {{ background: transparent; border: none; }}
        {product_field_style()}
        QDialog#{object_name} QPushButton#PrimaryBtn {{
            background: {DesignTokens.primary}; color: white; border: 1px solid {DesignTokens.primary};
            border-radius: {DesignTokens.radius_md}px; min-height: {DesignTokens.control_height}px;
            padding: 0 12px; font-weight: 600;
        }}
        QDialog#{object_name} QPushButton#PrimaryBtn:hover {{ background: {DesignTokens.primary_hover}; }}
        QDialog#{object_name} QPushButton#PrimaryBtn:pressed {{ background: {DesignTokens.primary_pressed}; }}
        QDialog#{object_name} QPushButton#PrimaryBtn:disabled {{
            background: {DesignTokens.bg_disabled}; color: {DesignTokens.text_disabled};
            border-color: {DesignTokens.border_subtle};
        }}
        QDialog#{object_name} QPushButton#SecondaryBtn {{
            background: {DesignTokens.bg_main}; color: {DesignTokens.text_primary};
            border: 1px solid {DesignTokens.border}; border-radius: {DesignTokens.radius_md}px;
            min-height: {DesignTokens.control_height}px; padding: 0 12px;
        }}
        QDialog#{object_name} QPushButton#SecondaryBtn:hover {{ background: {DesignTokens.bg_hover}; }}
        QDialog#{object_name} QPushButton:focus {{ border-color: {DesignTokens.primary_focus}; }}
        QDialog#{object_name} QPushButton:disabled {{ color: {DesignTokens.text_disabled}; }}
        QDialog#{object_name} QTabWidget::pane {{ border: none; background: transparent; }}
        """
    )


class ProductPageHeader(QWidget):
    backRequested = Signal()
    primaryRequested = Signal()

    def __init__(self, title, subtitle="", parent=None, back_text="", primary_text=""):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        if back_text:
            self.back_button = QPushButton(back_text)
            self.back_button.setObjectName("ProductBackButton")
            self.back_button.setToolTip("返回会话 (Alt+Left)")
            self.back_button.setFixedHeight(DesignTokens.control_height)
            self.back_button.setStyleSheet(product_button_style("ghost"))
            self.back_button.clicked.connect(self.backRequested)
            layout.addWidget(self.back_button, 0, Qt.AlignTop)
        else:
            self.back_button = None
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(4)
        title_label = QLabel(str(title or ""))
        self.title_label = title_label
        title_label.setProperty("roleTitle", True)
        title_label.setStyleSheet(
            f"font-size: {DesignTokens.font_size_page}px; font-weight: 700; color: {DesignTokens.text_primary};"
        )
        text_layout.addWidget(title_label)
        self.subtitle_label = None
        if subtitle:
            self.subtitle_label = QLabel(str(subtitle))
            self.subtitle_label.setWordWrap(True)
            self.subtitle_label.setStyleSheet(
                f"font-size: {DesignTokens.font_size_meta}px; color: {DesignTokens.text_secondary};"
            )
            text_layout.addWidget(self.subtitle_label)
        layout.addLayout(text_layout, 1)
        self.primary_button = None
        if primary_text:
            self.primary_button = QPushButton(primary_text)
            self.primary_button.setObjectName("PrimaryBtn")
            self.primary_button.setFixedHeight(DesignTokens.control_height)
            self.primary_button.setStyleSheet(product_button_style("primary"))
            self.primary_button.clicked.connect(self.primaryRequested)
            layout.addWidget(self.primary_button, 0, Qt.AlignTop)
        bind_theme(self, self.refresh_theme, surface="management")

    def refresh_theme(self, _resolved=None):
        self.title_label.setStyleSheet(
            f"font-size: {DesignTokens.font_size_page}px; "
            f"font-weight: {DesignTokens.font_weight_bold}; "
            f"color: {DesignTokens.text_primary};"
        )
        if self.subtitle_label is not None:
            self.subtitle_label.setStyleSheet(
                f"font-size: {DesignTokens.font_size_meta}px; "
                f"color: {DesignTokens.text_secondary};"
            )
        if self.back_button is not None:
            self.back_button.setStyleSheet(product_button_style("ghost"))
        if self.primary_button is not None:
            self.primary_button.setStyleSheet(product_button_style("primary"))

    def set_title(self, title, subtitle=None):
        self.title_label.setText(str(title or ""))
        if subtitle is not None and self.subtitle_label is not None:
            self.subtitle_label.setText(str(subtitle or ""))


class ProductToolbar(QFrame):
    """One quiet row for search, filters and result metadata."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ProductToolbar")
        self.setStyleSheet(
            f"QFrame#ProductToolbar {{ background: transparent; border: none; "
            f"border-bottom: 1px solid {DesignTokens.separator}; }}"
        )
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 8)
        self.layout.setSpacing(8)
        self.search_input = None
        self.result_label = QLabel("")
        self.result_label.setStyleSheet(
            f"color: {DesignTokens.text_tertiary}; font-size: {DesignTokens.font_size_meta}px;"
        )
        bind_theme(self, self.refresh_theme, surface="management")

    def refresh_theme(self, _resolved=None):
        self.setStyleSheet(
            f"QFrame#ProductToolbar {{ background: transparent; border: none; "
            f"border-bottom: 1px solid {DesignTokens.management_border}; }}"
        )
        self.result_label.setStyleSheet(
            f"color: {DesignTokens.text_tertiary}; "
            f"font-size: {DesignTokens.font_size_meta}px;"
        )
        if self.search_input is not None:
            self.search_input.setStyleSheet(product_field_style())

    def add_search(self, placeholder="搜索"):
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(placeholder)
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMinimumWidth(220)
        self.search_input.setStyleSheet(product_field_style())
        self.layout.addWidget(self.search_input, 1)
        return self.search_input

    def add_widget(self, widget):
        self.layout.addWidget(widget)
        return widget

    def finish(self):
        self.layout.addStretch()
        self.layout.addWidget(self.result_label)

    def set_result_text(self, text):
        self.result_label.setText(str(text or ""))


class ProductSegmentedControl(QFrame):
    currentChanged = Signal(str)

    def __init__(self, items, current=None, parent=None):
        super().__init__(parent)
        self.setObjectName("ProductSegmentedControl")
        self.setStyleSheet(product_segmented_style())
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}
        for index, item in enumerate(items or []):
            key, label = item
            button = QPushButton(str(label))
            button.setCheckable(True)
            button.setProperty("segment", True)
            button.setMinimumHeight(DesignTokens.control_height_sm)
            button.clicked.connect(lambda checked=False, value=str(key): self.currentChanged.emit(value))
            self.group.addButton(button, index)
            self.buttons[str(key)] = button
            layout.addWidget(button)
        selected = str(current or (items[0][0] if items else ""))
        if selected in self.buttons:
            self.buttons[selected].setChecked(True)
        bind_theme(self, self.refresh_theme, surface="controls")

    def refresh_theme(self, _resolved=None):
        self.setStyleSheet(product_segmented_style())
        for button in self.buttons.values():
            button.setMinimumHeight(DesignTokens.control_height_sm)

    def set_current(self, key):
        key = str(key or "")
        if key in self.buttons:
            self.buttons[key].setChecked(True)


class ProductNavigationRow(QPushButton):
    """Flat selectable management row with complete interaction states."""

    def __init__(self, title="", subtitle="", parent=None):
        text = str(title or "")
        if subtitle:
            text += "\n" + str(subtitle)
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(DesignTokens.row_height_comfortable)
        self.setStyleSheet(
            f"QPushButton {{ text-align:left; padding:7px 10px; border:none; border-radius:{DesignTokens.radius_sm}px; "
            f"background:transparent; color:{DesignTokens.text_primary}; }}"
            f"QPushButton:hover {{ background:{DesignTokens.bg_hover}; }}"
            f"QPushButton:checked {{ background:{DesignTokens.primary_soft}; color:{DesignTokens.primary}; }}"
            f"QPushButton:focus {{ border:1px solid {DesignTokens.primary_focus}; }}"
            f"QPushButton:disabled {{ color:{DesignTokens.text_disabled}; background:transparent; }}"
        )
        bind_theme(self, self.refresh_theme, surface="management")

    def refresh_theme(self, _resolved=None):
        self.setMinimumHeight(DesignTokens.row_height_comfortable)
        self.setStyleSheet(
            f"QPushButton {{ text-align:left; padding:7px 10px; border:none; "
            f"border-radius:{DesignTokens.radius_sm}px; background:transparent; "
            f"color:{DesignTokens.text_primary}; }}"
            f"QPushButton:hover {{ background:{DesignTokens.bg_hover}; }}"
            f"QPushButton:checked {{ background:{DesignTokens.primary_soft}; color:{DesignTokens.primary}; }}"
            f"QPushButton:focus {{ border:1px solid {DesignTokens.primary_focus}; }}"
            f"QPushButton:disabled {{ color:{DesignTokens.text_disabled}; background:transparent; }}"
        )


class ProductMasterDetail(QFrame):
    """Responsive browse/detail host used by management centers."""

    detailVisibilityChanged = Signal(bool)

    def __init__(self, browse, detail, threshold=DesignTokens.management_split_threshold, parent=None):
        super().__init__(parent)
        self.setObjectName("ProductMasterDetail")
        self.setStyleSheet("QFrame#ProductMasterDetail { background: transparent; border: none; }")
        self.threshold = int(threshold)
        self.browse = browse
        self.detail = detail
        self.detail_visible = False
        self._compact = None
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(1)
        self.splitter.addWidget(browse)
        self.splitter.addWidget(detail)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([320, 640])
        self.host = self.splitter
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.splitter)

    def _apply_compact_visibility(self):
        show_detail = bool(self.detail_visible)
        self.browse.setVisible(not show_detail)
        self.detail.setVisible(show_detail)
        total = max(self.width(), 320)
        self.splitter.setSizes(
            [0, total] if show_detail else [total, 0]
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        compact = self.width() < self.threshold
        if compact == self._compact:
            return
        self._compact = compact
        if compact:
            self._apply_compact_visibility()
        else:
            self.browse.show()
            self.detail.show()
            total = max(self.width(), 640)
            browse_width = min(340, max(240, total // 3))
            self.splitter.setSizes(
                [browse_width, max(320, total - browse_width)]
            )

    def show_detail(self):
        self.detail_visible = True
        if self._compact:
            self._apply_compact_visibility()
        self.detailVisibilityChanged.emit(True)

    def show_browse(self):
        self.detail_visible = False
        if self._compact:
            self._apply_compact_visibility()
        self.detailVisibilityChanged.emit(False)


class ProductInlineNotice(QFrame):
    def __init__(self, text="", tone="neutral", parent=None):
        super().__init__(parent)
        self.setObjectName("ProductInlineNotice")
        self.label = QLabel(str(text or ""))
        self.label.setWordWrap(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.addWidget(self.label)
        self.set_tone(tone)
        bind_theme(self, self.refresh_theme, surface="feedback")

    def set_tone(self, tone):
        self._tone = str(tone or "neutral")
        tones = {
            "neutral": (DesignTokens.bg_secondary, DesignTokens.text_secondary),
            "info": (DesignTokens.info_bg, DesignTokens.info_text),
            "success": (DesignTokens.success_bg, DesignTokens.success_text),
            "warning": (DesignTokens.warning_bg, DesignTokens.warning_text),
            "error": (DesignTokens.error_bg, DesignTokens.error_text),
        }
        bg, fg = tones.get(self._tone, tones["neutral"])
        self.setStyleSheet(
            f"QFrame#ProductInlineNotice {{ background:{bg}; border:none; border-radius:{DesignTokens.radius_sm}px; }}"
            f"QFrame#ProductInlineNotice QLabel {{ color:{fg}; background:transparent; border:none; }}"
        )

    def refresh_theme(self, _resolved=None):
        self.set_tone(getattr(self, "_tone", "neutral"))

    def set_text(self, text, tone=None):
        self.label.setText(str(text or ""))
        if tone is not None:
            self.set_tone(tone)


class ProductSection(QFrame):
    def __init__(self, title="", subtitle="", kind="panel", parent=None):
        super().__init__(parent)
        self.setProperty("productSurface", kind)
        self.setStyleSheet(product_surface_style(kind))
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(4, 12, 4, 16)
        self.layout.setSpacing(10)
        self.kind = str(kind or "panel")
        self.title_label = None
        self.subtitle_label = None
        if title:
            title_label = QLabel(title)
            self.title_label = title_label
            title_label.setStyleSheet(
                f"font-size: {DesignTokens.font_size_section}px; font-weight: 600; color: {DesignTokens.text_primary};"
            )
            self.layout.addWidget(title_label)
        if subtitle:
            subtitle_label = QLabel(subtitle)
            self.subtitle_label = subtitle_label
            subtitle_label.setWordWrap(True)
            subtitle_label.setStyleSheet(
                f"font-size: {DesignTokens.font_size_meta}px; color: {DesignTokens.text_secondary};"
            )
            self.layout.addWidget(subtitle_label)
        bind_theme(self, self.refresh_theme, surface="management")

    def refresh_theme(self, _resolved=None):
        self.setStyleSheet(product_surface_style(self.kind))
        if self.title_label is not None:
            self.title_label.setStyleSheet(
                f"font-size: {DesignTokens.font_size_section}px; "
                f"font-weight: {DesignTokens.font_weight_semibold}; "
                f"color: {DesignTokens.text_primary};"
            )
        if self.subtitle_label is not None:
            self.subtitle_label.setStyleSheet(
                f"font-size: {DesignTokens.font_size_meta}px; "
                f"color: {DesignTokens.text_secondary};"
            )


class ProductStatusBadge(QLabel):
    def __init__(self, text="", tone="neutral", parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)
        self.set_tone(tone)
        bind_theme(self, self.refresh_theme, surface="feedback")

    def set_tone(self, tone):
        self._tone = str(tone or "neutral")
        tones = {
            "neutral": (DesignTokens.muted_chip_bg, DesignTokens.muted_chip_text),
            "primary": (DesignTokens.primary_soft, DesignTokens.primary),
            "success": (DesignTokens.success_bg, DesignTokens.success_text),
            "warning": (DesignTokens.warning_bg, DesignTokens.warning_text),
            "error": (DesignTokens.error_bg, DesignTokens.error_text),
        }
        bg, fg = tones.get(self._tone, tones["neutral"])
        self.setStyleSheet(
            f"background: {bg}; color: {fg}; border: none; border-radius: 6px; "
            "padding: 3px 7px; font-size: 11px; font-weight: 600;"
        )

    def refresh_theme(self, _resolved=None):
        self.set_tone(getattr(self, "_tone", "neutral"))


class ProductEmptyState(QFrame):
    def __init__(
        self,
        title,
        description="",
        action_text="",
        parent=None,
        *,
        appearance="surface",
        icon=None,
        action_kind="primary",
    ):
        super().__init__(parent)
        self.setObjectName("ProductEmptyState")
        self.appearance = str(appearance or "surface").strip().lower()
        if self.appearance == "plain":
            self.setStyleSheet("QFrame#ProductEmptyState { background: transparent; border: none; }")
            self.setMaximumHeight(16777215)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        else:
            self.setProperty("productSurface", "subtle")
            self.setStyleSheet(product_surface_style("subtle"))
            self.setMaximumHeight(180)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QVBoxLayout(self)
        vertical_margin = 24 if self.appearance != "plain" else 20
        layout.setContentsMargins(20, vertical_margin, 20, vertical_margin)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignCenter)
        self.icon_label = None
        if isinstance(icon, QIcon) and not icon.isNull():
            self.icon_label = QLabel(self)
            self.icon_label.setAlignment(Qt.AlignCenter)
            self.icon_label.setFixedSize(32, 32)
            self.icon_label.setPixmap(icon.pixmap(18, 18))
            self.icon_label.setStyleSheet(
                f"background: {DesignTokens.primary_soft}; border: none; border-radius: 8px;"
            )
            layout.addWidget(self.icon_label, 0, Qt.AlignCenter)
        self.title_label = QLabel(title, self)
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setStyleSheet(
            f"font-size: {DesignTokens.font_size_section}px; font-weight: 600; color: {DesignTokens.text_primary};"
        )
        layout.addWidget(self.title_label)
        self.description_label = QLabel(description, self)
        self.description_label.setAlignment(Qt.AlignCenter)
        self.description_label.setWordWrap(True)
        self.description_label.setMaximumWidth(280)
        self.description_label.setStyleSheet(
            f"font-size: {DesignTokens.font_size_meta}px; color: {DesignTokens.text_secondary};"
        )
        self.description_label.setVisible(bool(description))
        layout.addWidget(self.description_label)
        self.action_button = None
        if action_text:
            self.action_button = QPushButton(action_text, self)
            self.action_button.setObjectName("PrimaryBtn" if action_kind == "primary" else "SecondaryBtn")
            self.action_button.setCursor(Qt.PointingHandCursor)
            self.action_button.setStyleSheet(product_button_style(action_kind))
            layout.addWidget(self.action_button, 0, Qt.AlignCenter)
        self.action_kind = str(action_kind or "primary")
        bind_theme(self, self.refresh_theme, surface="management")

    def refresh_theme(self, _resolved=None):
        if self.appearance == "plain":
            self.setStyleSheet(
                "QFrame#ProductEmptyState { background: transparent; border: none; }"
            )
        else:
            self.setStyleSheet(product_surface_style("subtle"))
        if self.icon_label is not None:
            self.icon_label.setStyleSheet(
                f"background: {DesignTokens.primary_soft}; border: none; "
                f"border-radius: {DesignTokens.radius_md}px;"
            )
        self.title_label.setStyleSheet(
            f"font-size: {DesignTokens.font_size_section}px; "
            f"font-weight: {DesignTokens.font_weight_semibold}; "
            f"color: {DesignTokens.text_primary};"
        )
        self.description_label.setStyleSheet(
            f"font-size: {DesignTokens.font_size_meta}px; "
            f"color: {DesignTokens.text_secondary};"
        )
        if self.action_button is not None:
            self.action_button.setStyleSheet(product_button_style(self.action_kind))

    def set_content(self, title, description=""):
        self.title_label.setText(str(title or ""))
        self.description_label.setText(str(description or ""))
        self.description_label.setVisible(bool(description))

    def set_action(self, text="", visible=True):
        if self.action_button is None:
            raise RuntimeError("ProductEmptyState action button was not configured.")
        self.action_button.setText(str(text or ""))
        self.action_button.setVisible(bool(text) and bool(visible))


class ProductDataRow(QFrame):
    def __init__(self, title="", subtitle="", parent=None):
        super().__init__(parent)
        self.setProperty("productRow", True)
        self.setStyleSheet(
            f'QFrame[productRow="true"] {{ background: transparent; border: none; '
            f"border-bottom: 1px solid {DesignTokens.separator}; }}"
        )
        self.row_layout = QHBoxLayout(self)
        self.row_layout.setContentsMargins(8, 8, 8, 8)
        self.row_layout.setSpacing(10)
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setStyleSheet(f"font-weight: 600; color: {DesignTokens.text_primary};")
        text_layout.addWidget(self.title_label)
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setStyleSheet(
            f"font-size: {DesignTokens.font_size_meta}px; color: {DesignTokens.text_secondary};"
        )
        text_layout.addWidget(self.subtitle_label)
        self.row_layout.addLayout(text_layout, 1)
        bind_theme(self, self.refresh_theme, surface="management")

    def refresh_theme(self, _resolved=None):
        self.setStyleSheet(
            f'QFrame[productRow="true"] {{ background: transparent; border: none; '
            f"border-bottom: 1px solid {DesignTokens.management_border}; }}"
        )
        self.title_label.setStyleSheet(
            f"font-weight: {DesignTokens.font_weight_semibold}; "
            f"color: {DesignTokens.text_primary};"
        )
        self.subtitle_label.setStyleSheet(
            f"font-size: {DesignTokens.font_size_meta}px; "
            f"color: {DesignTokens.text_secondary};"
        )


class ProductActionBar(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("productSurface", "panel")
        self.setStyleSheet(
            f'QFrame[productSurface="panel"] {{ background: {DesignTokens.bg_main}; border: none; '
            f"border-top: 1px solid {DesignTokens.separator}; }}"
        )
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(0, 10, 0, 0)
        self.layout.setSpacing(8)
        self.layout.addStretch()
        bind_theme(self, self.refresh_theme, surface="management")

    def refresh_theme(self, _resolved=None):
        self.setStyleSheet(
            f'QFrame[productSurface="panel"] {{ background: {DesignTokens.management_panel_bg}; '
            f"border: none; border-top: 1px solid {DesignTokens.management_border}; }}"
        )
