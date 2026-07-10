"""Small, scoped PySide6 primitives for the Cowork product language.

The helpers in this module deliberately avoid broad QWidget selectors. A
surface opts into a role through an object name or a dynamic property, which
prevents parent styling from leaking into labels and nested controls.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.theme import DesignTokens


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
            "#fde8e8",
            "#fbd5d5",
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
            padding: 0 9px; selection-background-color: {DesignTokens.primary_soft};
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
    return f"""
        {selector} {{
            background: {DesignTokens.bg_code}; color: {DesignTokens.text_primary};
            border: 1px solid {DesignTokens.border_subtle}; border-radius: {DesignTokens.radius_md}px;
            padding: 10px; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 12px;
            selection-background-color: {DesignTokens.primary_soft};
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
    def __init__(self, title, subtitle="", parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        title_label = QLabel(str(title or ""))
        title_label.setProperty("roleTitle", True)
        title_label.setStyleSheet(
            f"font-size: {DesignTokens.font_size_page}px; font-weight: 700; color: {DesignTokens.text_primary};"
        )
        layout.addWidget(title_label)
        self.subtitle_label = None
        if subtitle:
            self.subtitle_label = QLabel(str(subtitle))
            self.subtitle_label.setWordWrap(True)
            self.subtitle_label.setStyleSheet(
                f"font-size: {DesignTokens.font_size_meta}px; color: {DesignTokens.text_secondary};"
            )
            layout.addWidget(self.subtitle_label)


class ProductSection(QFrame):
    def __init__(self, title="", subtitle="", kind="panel", parent=None):
        super().__init__(parent)
        self.setProperty("productSurface", kind)
        self.setStyleSheet(product_surface_style(kind))
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(4, 12, 4, 16)
        self.layout.setSpacing(10)
        if title:
            title_label = QLabel(title)
            title_label.setStyleSheet(
                f"font-size: {DesignTokens.font_size_section}px; font-weight: 600; color: {DesignTokens.text_primary};"
            )
            self.layout.addWidget(title_label)
        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setWordWrap(True)
            subtitle_label.setStyleSheet(
                f"font-size: {DesignTokens.font_size_meta}px; color: {DesignTokens.text_secondary};"
            )
            self.layout.addWidget(subtitle_label)


class ProductStatusBadge(QLabel):
    TONES = {
        "neutral": (DesignTokens.muted_chip_bg, DesignTokens.muted_chip_text),
        "primary": (DesignTokens.primary_soft, DesignTokens.primary),
        "success": (DesignTokens.success_bg, DesignTokens.success_text),
        "warning": (DesignTokens.warning_bg, DesignTokens.warning_text),
        "error": (DesignTokens.error_bg, DesignTokens.error_text),
    }

    def __init__(self, text="", tone="neutral", parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)
        self.set_tone(tone)

    def set_tone(self, tone):
        bg, fg = self.TONES.get(tone, self.TONES["neutral"])
        self.setStyleSheet(
            f"background: {bg}; color: {fg}; border: none; border-radius: 6px; "
            "padding: 3px 7px; font-size: 11px; font-weight: 600;"
        )


class ProductEmptyState(QFrame):
    def __init__(self, title, description="", action_text="", parent=None):
        super().__init__(parent)
        self.setProperty("productSurface", "subtle")
        self.setStyleSheet(product_surface_style("subtle"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 24, 20, 24)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignCenter)
        title_label = QLabel(title)
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet(
            f"font-size: {DesignTokens.font_size_section}px; font-weight: 600; color: {DesignTokens.text_primary};"
        )
        layout.addWidget(title_label)
        if description:
            description_label = QLabel(description)
            description_label.setAlignment(Qt.AlignCenter)
            description_label.setWordWrap(True)
            description_label.setStyleSheet(
                f"font-size: {DesignTokens.font_size_meta}px; color: {DesignTokens.text_secondary};"
            )
            layout.addWidget(description_label)
        self.action_button = None
        if action_text:
            self.action_button = QPushButton(action_text)
            self.action_button.setObjectName("PrimaryBtn")
            layout.addWidget(self.action_button, 0, Qt.AlignCenter)


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
