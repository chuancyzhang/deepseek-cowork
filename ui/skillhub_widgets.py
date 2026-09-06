"""Small presentation widgets for the existing capability store."""
from PySide6.QtCore import Qt, Signal, QBuffer, QIODevice
from PySide6.QtGui import QPainter, QColor, QTextLayout, QTextOption, QImageReader, QPixmap
from PySide6.QtWidgets import QWidget, QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QSizePolicy

from core.theme import DesignTokens as T


def compact_count(value):
    number = max(0, int(value or 0))
    return f"{number / 1000:.0f}k" if number >= 10000 else f"{number:,}"


def decode_icon(data):
    buffer = QBuffer()
    buffer.setData(data)
    buffer.open(QIODevice.ReadOnly)
    reader = QImageReader(buffer)
    size = reader.size()
    if not size.isValid() or size.width() > 2048 or size.height() > 2048:
        raise ValueError("图标尺寸无效或超过 2048 像素")
    reader.setScaledSize(size.scaled(96, 96, Qt.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        raise ValueError("图标无法解码")
    return QPixmap.fromImage(image)


class ClampedText(QWidget):
    """Paint at most N lines, using Qt's Unicode-aware line breaking."""
    def __init__(self, text, lines=1, strong=False, parent=None):
        super().__init__(parent)
        self.text = " ".join(str(text).split())
        self.lines = lines
        self.strong = strong
        font = self.font()
        font.setPixelSize(T.font_size_body if strong else T.font_size_meta)
        font.setBold(strong)
        self.setFont(font)
        self.setFixedHeight(self.fontMetrics().lineSpacing() * lines + 2)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setToolTip(self.text)
        self.setAccessibleName(self.text)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(QColor(T.text_primary if self.strong else T.text_secondary))
        layout = QTextLayout(self.text, self.font())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        layout.beginLayout()
        encoded = self.text.encode("utf-16-le")
        for index in range(self.lines):
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max(1, self.width()))
            start = line.textStart() * 2
            end = start + line.textLength() * 2
            text = encoded[start:(None if index == self.lines - 1 else end)].decode("utf-16-le")
            text = self.fontMetrics().elidedText(text, Qt.ElideRight, self.width())
            painter.drawText(0, index * self.fontMetrics().lineSpacing() + self.fontMetrics().ascent(), text)
        layout.endLayout()


def store_columns(width):
    return max(1, min(4, (width + T.spacing_md) // (280 + T.spacing_md)))


def style_store_card(card):
    card.setMinimumWidth(0)
    card.setFixedHeight(max(174, int(2 * T.spacing_md + 3 * T.spacing_sm
                                    + max(32, T.font_size_body * 1.5)
                                    + T.font_size_meta * 4.5 + 24)))
    name = card.objectName()
    card.setStyleSheet(
        f"QFrame#{name} {{background: {T.bg_main}; border: 1px solid {T.border_subtle}; border-radius: {T.radius_lg}px;}}"
        f"QFrame#{name}:hover, QFrame#{name}:focus {{border-color: {T.accent_ai};}}"
    )


class SkillHubCard(QFrame):
    opened = Signal()
    installRequested = Signal()

    def __init__(self, skill, state=None, installed=None, busy=False, parent=None):
        super().__init__(parent)
        state = state or {}
        if state.get("stage") == "error" and state.get("version") != skill.get("version"):
            state = {}
        self.setObjectName("SkillHubCard")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(f"查看 {skill['name']} 详情")
        style_store_card(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.spacing_md, T.spacing_md, T.spacing_md, T.spacing_md)
        layout.setSpacing(T.spacing_sm)
        header = QHBoxLayout()
        self.icon = QLabel(str(skill['name'])[:1])
        self.icon.setAlignment(Qt.AlignCenter)
        self.icon.setFixedSize(32, 32)
        self.icon.setStyleSheet(f"background: {T.bg_app}; color: {T.accent_ai}; border-radius: {T.radius_md}px; font-weight: 600;")
        self.icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.icon_url = str(skill.get("iconUrl") or "")
        header.addWidget(self.icon)
        header.addWidget(ClampedText(skill['name'], strong=True), 1)
        stage = state.get("stage")
        label = "…" if stage == "running" else "重试" if stage == "error" else "✓" if installed else "+"
        self.action = QPushButton(label)
        self.action.setObjectName("SkillHubInstallAction")
        self.action.setFixedSize(40, 32)
        self.action.setStyleSheet(
            f"QPushButton#SkillHubInstallAction {{background: {T.bg_app}; color: {T.text_primary}; border: none; border-radius: {T.radius_sm}px; padding: 0; font-size: {T.font_size_section}px;}}"
            f"QPushButton#SkillHubInstallAction:hover {{color: {T.accent_ai};}}"
            f"QPushButton#SkillHubInstallAction:disabled {{color: {T.text_disabled};}}"
        )
        self.action.setAccessibleName(f"{'查看已安装技能' if installed else '安装技能'} {skill['name']}")
        self.action.setToolTip(state.get("message") or ("已安装，查看详情" if installed else f"安装 {skill.get('version') or '版本未提供'}"))
        self.action.setEnabled(bool(installed) or (not busy and bool(skill.get("version"))))
        self.action.clicked.connect(self.opened.emit if installed else self.installRequested.emit)
        header.addWidget(self.action)
        layout.addLayout(header)
        layout.addWidget(ClampedText(skill.get("description_zh") or skill.get("description") or "暂无简介", lines=2))
        layout.addStretch()
        stats = f"↓ {compact_count(skill.get('downloads'))}    ☆ {compact_count(skill.get('stars'))}"
        status = state.get("message", "") if stage in {"error", "running"} else (f"已安装 {installed['version']}" if installed else "")
        footer = QHBoxLayout()
        footer.addWidget(ClampedText(stats), 1)
        if status:
            footer.addWidget(ClampedText(status), 1)
        layout.addLayout(footer)

    def set_icon(self, url, pixmap):
        if url != self.icon_url:
            return
        if isinstance(pixmap, QPixmap):
            self.icon.setPixmap(pixmap.scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.icon.setText("!")
            self.icon.setToolTip("图标加载失败；技能仍可查看和安装")

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.opened.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in {Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space}:
            self.opened.emit()
            event.accept()
        else:
            super().keyPressEvent(event)
