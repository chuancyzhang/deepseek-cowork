"""Conversation-owned questions and submission receipts."""
import copy
import json

import qtawesome as qta
from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractScrollArea, QBoxLayout, QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)
from core.clarify_mode import normalize_pending_clarify_questions
from core.theme import DesignTokens, bind_theme
from ui.primitives import product_button_style, product_field_style


class _ContentButton(QPushButton):
    """A keyboard-accessible button whose wrapped child labels own its height."""

    def __init__(self, parent=None):
        super().__init__(parent)
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self):
        return self.layout().sizeHint() if self.layout() is not None else super().sizeHint()

    def minimumSizeHint(self):
        return QSize(0, self.minimumHeight())

    def heightForWidth(self, width):
        return max(self.minimumHeight(), self.layout().totalHeightForWidth(width))


def _label(text, parent, *, passive=False):
    label = QLabel(str(text or ""), parent)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    if passive:
        label.setAttribute(Qt.WA_TransparentForMouseEvents)
    return label


class InteractionChoiceButton(_ContentButton):
    """Theme-owned radio/check row with wrapping text and an explicit selection mark."""

    def __init__(self, label, description="", value="", parent=None, *, recommended=False, multiple=False):
        super().__init__(parent)
        self.setObjectName("InteractionChoice")
        self.setCheckable(True)
        self.setProperty("option_value", str(value or label or ""))
        self.option_label = str(label or "选项")
        self.recommended = bool(recommended)
        self.multiple = bool(multiple)
        self.setAccessibleName(self.option_label + ("，推荐" if recommended else ""))
        self.setAccessibleDescription(str(description or ""))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)
        self.indicator = QLabel(self)
        self.indicator.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.indicator.setFixedSize(18, 20)
        layout.addWidget(self.indicator, 0, Qt.AlignTop)
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(3)
        label_row = QHBoxLayout()
        label_row.setSpacing(8)
        self.title_label = _label(self.option_label, self, passive=True)
        label_row.addWidget(self.title_label, 1)
        self.recommendation_label = _label("推荐", self, passive=True)
        self.recommendation_label.setWordWrap(False)
        self.recommendation_label.setVisible(self.recommended)
        label_row.addWidget(self.recommendation_label, 0, Qt.AlignTop)
        text_layout.addLayout(label_row)
        self.description_label = _label(description, self, passive=True)
        self.description_label.setVisible(bool(description))
        text_layout.addWidget(self.description_label)
        layout.addLayout(text_layout, 1)
        self.toggled.connect(self._refresh_indicator)
        self.refresh_theme()
        bind_theme(self, self.refresh_theme, surface="feedback")

    def _refresh_indicator(self, _checked=None):
        name = ("fa5s.check-square" if self.isChecked() else "fa5.square") if self.multiple else (
            "fa5s.dot-circle" if self.isChecked() else "fa5.circle")
        color = DesignTokens.text_primary if self.isEnabled() else DesignTokens.text_disabled
        self.indicator.setPixmap(qta.icon(name, color=color).pixmap(16, 16))

    def refresh_theme(self, _resolved=None):
        self.setMinimumHeight(DesignTokens.control_height)
        self.setStyleSheet(
            f"QPushButton#InteractionChoice {{ background: {DesignTokens.overlay_bg}; border: 1px solid transparent; "
            f"border-radius: {DesignTokens.radius_sm}px; }}"
            f"QPushButton#InteractionChoice:hover {{ background: {DesignTokens.bg_hover}; }}"
            f"QPushButton#InteractionChoice:checked {{ background: {DesignTokens.primary_soft}; border-color: {DesignTokens.overlay_border}; }}"
            f"QPushButton#InteractionChoice:focus {{ border: 2px solid {DesignTokens.primary_focus}; }}"
        )
        self.title_label.setStyleSheet(f"background: transparent; border: none; color: {DesignTokens.overlay_text}; font-size: {DesignTokens.font_size_body}px;")
        self.description_label.setStyleSheet(f"background: transparent; border: none; color: {DesignTokens.text_secondary}; font-size: {DesignTokens.font_size_meta}px;")
        self.recommendation_label.setStyleSheet(
            f"background: {DesignTokens.overlay_bg}; color: {DesignTokens.overlay_text}; border: 1px solid {DesignTokens.overlay_border}; "
            f"border-radius: 3px; padding: 0 4px; font-size: {DesignTokens.font_size_caption}px;")
        self.indicator.setStyleSheet("background: transparent; border: none;")
        self._refresh_indicator()
        self.updateGeometry()


class QuestionnaireSection(QFrame):
    toggled = Signal(bool)
    changed = Signal()

    def __init__(self, question, number, parent, *, recommend=True):
        super().__init__(parent)
        self.question = question
        self.setObjectName("QuestionnaireSection")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = _ContentButton(self)
        self.header.setObjectName("QuestionnaireHeader")
        self.header.setCheckable(True)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(6, 10, 6, 10)
        header_layout.setSpacing(10)
        self.number_label = _label(number, self.header, passive=True)
        self.number_label.setFixedWidth(18)
        header_layout.addWidget(self.number_label, 0, Qt.AlignTop)
        text_layout = QVBoxLayout()
        text_layout.setSpacing(3)
        self.title_label = _label(question["question"], self.header, passive=True)
        self.summary_label = _label("", self.header, passive=True)
        text_layout.addWidget(self.title_label)
        text_layout.addWidget(self.summary_label)
        header_layout.addLayout(text_layout, 1)
        self.arrow = QLabel(self.header)
        self.arrow.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.arrow.setFixedSize(16, 18)
        header_layout.addWidget(self.arrow, 0, Qt.AlignVCenter)
        layout.addWidget(self.header)
        self.body = QWidget(self)
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(24, 0, 0, 12)
        body_layout.setSpacing(4)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = []
        for option in question.get("options") or []:
            value = str(option.get("value") or option.get("label") or "")
            if value == "__custom__":
                continue
            button = InteractionChoiceButton(option.get("label"), option.get("description"), value, self.body,
                                             recommended=recommend and not self.buttons)
            self.group.addButton(button)
            self.buttons.append(button)
            body_layout.addWidget(button)
        custom_row = QHBoxLayout()
        custom_row.setContentsMargins(6, 6, 0, 0)
        custom_row.setSpacing(8)
        self.custom_label = _label("其他回答", self.body)
        self.custom_label.setWordWrap(False)
        self.input = QLineEdit(self.body)
        self.input.setMinimumWidth(0)
        self.input.setPlaceholderText("直接填写你的想法")
        self.input.setAccessibleName(question["question"] + "：其他回答")
        self.custom_label.setBuddy(self.input)
        custom_row.addWidget(self.custom_label)
        custom_row.addWidget(self.input, 1)
        body_layout.addLayout(custom_row)
        layout.addWidget(self.body)
        self.body.hide()
        if recommend and self.buttons:
            self.buttons[0].setChecked(True)
        for button in self.buttons:
            button.toggled.connect(self._choice_changed)
        self.input.textChanged.connect(self._text_changed)
        self.header.toggled.connect(self._toggle)
        self._sync_summary()
        self.refresh_theme()
        bind_theme(self, self.refresh_theme, surface="feedback")

    def _choice_changed(self, checked):
        if checked:
            self.input.clear()
        self._sync_summary()
        self.changed.emit()

    def _text_changed(self, text):
        if text.strip():
            self.group.setExclusive(False)
            for button in self.buttons:
                button.setChecked(False)
            self.group.setExclusive(True)
        self._sync_summary()
        self.changed.emit()

    def _sync_summary(self):
        selected = self.group.checkedButton()
        answer = self.input.text().strip() or (selected.option_label if selected else "未选择")
        suffix = " · 推荐" if selected is not None and selected.recommended else ""
        self.summary_label.setText(answer + suffix)
        self.header.setAccessibleName(self.question["question"] + "：" + answer + suffix)

    def _toggle(self, expanded):
        self.body.setVisible(expanded)
        self.summary_label.setVisible(not expanded)
        self._refresh_arrow()
        self.toggled.emit(expanded)
        self.updateGeometry()

    def _refresh_arrow(self):
        self.arrow.setPixmap(qta.icon("fa5s.chevron-down" if self.header.isChecked() else "fa5s.chevron-right",
                                      color=DesignTokens.text_secondary).pixmap(12, 12))

    def refresh_theme(self, _resolved=None):
        self.setStyleSheet(f"QFrame#QuestionnaireSection {{ background: transparent; border: none; border-top: 1px solid {DesignTokens.separator}; }}")
        self.header.setStyleSheet(
            f"QPushButton#QuestionnaireHeader {{ background: transparent; border: 1px solid transparent; text-align: left; border-radius: {DesignTokens.radius_sm}px; }}"
            f"QPushButton#QuestionnaireHeader:hover {{ background: {DesignTokens.bg_hover}; }}"
            f"QPushButton#QuestionnaireHeader:focus {{ border-color: {DesignTokens.primary_focus}; }}")
        self.title_label.setStyleSheet(f"background: transparent; border: none; color: {DesignTokens.overlay_text}; font-size: {DesignTokens.font_size_body}px; font-weight: {DesignTokens.font_weight_medium};")
        for label in (self.summary_label, self.number_label, self.custom_label):
            label.setStyleSheet(f"background: transparent; border: none; color: {DesignTokens.text_secondary}; font-size: {DesignTokens.font_size_meta}px;")
        self.input.setStyleSheet(product_field_style())
        self.input.setMinimumHeight(DesignTokens.control_height)
        self.arrow.setStyleSheet("background: transparent; border: none;")
        self._refresh_arrow()


class QuestionnaireForm(QWidget):
    """One expanded question, with a bounded question area and reachable actions."""

    def __init__(self, request, parent):
        super().__init__(parent)
        self.request = request
        self.sections = []
        self.question_controls = []
        self._outer_viewport = None
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.timeout.connect(self._fit_questions)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        heading = QHBoxLayout()
        self.title_label = _label(request.get("title") or "需要你的输入", self)
        self.status_label = _label("待回答", self)
        self.status_label.setWordWrap(False)
        heading.addWidget(self.title_label, 1)
        heading.addWidget(self.status_label, 0, Qt.AlignTop)
        layout.addLayout(heading)
        self.message_label = _label(request.get("message"), self)
        self.message_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.message_label.setVisible(bool(request.get("message")))
        layout.addWidget(self.message_label)
        self.recommend = str((request.get("metadata") or {}).get("purpose") or request.get("purpose") or "") != "grill_checkpoint"
        self.hint_label = _label("已选中推荐项，你可以修改后一起提交。" if self.recommend else "请选择下一步，提交后生效。", self)
        layout.addWidget(self.hint_label)
        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("QuestionnaireScroll")
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setMinimumWidth(0)
        self.questions_widget = QWidget(self.scroll)
        self.questions_widget.setObjectName("QuestionnaireQuestions")
        self.questions_layout = QVBoxLayout(self.questions_widget)
        self.questions_layout.setContentsMargins(0, 0, 6, 0)
        self.questions_layout.setSpacing(0)
        self.questions_layout.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(self.questions_widget)
        self.questions_widget.installEventFilter(self)
        self.scroll.viewport().installEventFilter(self)
        layout.addWidget(self.scroll)
        for index, question in enumerate(normalize_pending_clarify_questions(request.get("questions") or [])):
            section = QuestionnaireSection(question, index + 1, self.questions_widget, recommend=self.recommend)
            self.sections.append(section)
            self.questions_layout.addWidget(section)
            self.question_controls.append({"id": question["id"], "group": section.group, "buttons": section.buttons,
                                           "input": section.input, "section": section})
            section.toggled.connect(lambda expanded, item=section: self._expand(item, expanded))
            section.changed.connect(self._update_count)
        self.validation_label = _label("", self)
        self.validation_label.hide()
        layout.addWidget(self.validation_label)
        self.actions = QWidget(self)
        self.actions.setObjectName("QuestionnaireActions")
        self.actions_layout = QBoxLayout(QBoxLayout.LeftToRight, self.actions)
        self.actions_layout.setContentsMargins(0, 12, 0, 0)
        self.actions_layout.setSpacing(8)
        self.count_label = _label("", self.actions)
        self.actions_layout.addWidget(self.count_label, 1)
        buttons = QWidget(self.actions)
        button_layout = QHBoxLayout(buttons)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(8)
        self.cancel_btn = QPushButton("取消", buttons)
        self.submit_btn = QPushButton("提交回答", buttons)
        button_layout.addWidget(self.cancel_btn)
        button_layout.addWidget(self.submit_btn)
        self.actions_layout.addWidget(buttons, 0, Qt.AlignRight)
        layout.addWidget(self.actions)
        if self.sections:
            self.sections[0].header.setChecked(True)
        self._update_count()
        self.refresh_theme()
        bind_theme(self, self.refresh_theme, surface="feedback")

    def _expand(self, section, expanded):
        if expanded:
            for other in self.sections:
                if other is not section:
                    other.header.setChecked(False)
            self.scroll.ensureWidgetVisible(section.header, 0, 0)
        self._layout_timer.start(0)

    def _update_count(self):
        recommended = sum(bool(section.group.checkedButton() and section.group.checkedButton().recommended) for section in self.sections)
        self.count_label.setText(f"{len(self.sections)} 个问题" + (f" · {recommended} 项采用推荐" if self.recommend else ""))

    def eventFilter(self, obj, event):
        if event.type() in {QEvent.Resize, QEvent.LayoutRequest}:
            self._layout_timer.start(0)
        return super().eventFilter(obj, event)

    def showEvent(self, event):
        super().showEvent(event)
        ancestor = self.parentWidget()
        while ancestor is not None and not isinstance(ancestor, QAbstractScrollArea):
            ancestor = ancestor.parentWidget()
        if ancestor is not None and self._outer_viewport is not ancestor.viewport():
            self._outer_viewport = ancestor.viewport()
            self._outer_viewport.installEventFilter(self)
        self._layout_timer.start(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.actions_layout.setDirection(QBoxLayout.TopToBottom if self.width() < 420 else QBoxLayout.LeftToRight)
        for section in self.sections:
            section.body.layout().setContentsMargins(0 if self.width() < 420 else 24, 0, 0, 12)
        self._layout_timer.start(0)

    def _fit_questions(self):
        if self.isHidden():
            return
        available = 420
        if self._outer_viewport is not None:
            other_height = self.title_label.sizeHint().height() + self.hint_label.sizeHint().height() + self.actions.sizeHint().height() + 80
            if not self.message_label.isHidden():
                other_height += self.message_label.heightForWidth(max(1, self.width()))
            available = max(160, min(420, self._outer_viewport.height() - other_height))
        width = max(1, self.scroll.viewport().width())
        desired = max(1, self.questions_layout.totalHeightForWidth(width))
        target = min(available, desired)
        if self.scroll.height() != target:
            self.scroll.setFixedHeight(target)

    def missing_section(self):
        return next((section for section in self.sections if not section.group.checkedButton() and not section.input.text().strip()), None)

    def refresh_theme(self, _resolved=None):
        self.title_label.setStyleSheet(f"background: transparent; border: none; color: {DesignTokens.overlay_text}; font-size: {DesignTokens.font_size_section}px; font-weight: {DesignTokens.font_weight_medium};")
        for label in (self.message_label, self.hint_label, self.count_label, self.status_label):
            label.setStyleSheet(f"background: transparent; border: none; color: {DesignTokens.text_secondary}; font-size: {DesignTokens.font_size_meta}px;")
        self.scroll.setStyleSheet(f"QScrollArea#QuestionnaireScroll, QWidget#QuestionnaireQuestions {{ border: none; background: {DesignTokens.overlay_bg}; }}")
        self.actions.setStyleSheet(f"QWidget#QuestionnaireActions {{ background: {DesignTokens.overlay_bg}; border: none; border-top: 1px solid {DesignTokens.separator}; }}")
        self.submit_btn.setStyleSheet(product_button_style("primary"))
        self.cancel_btn.setStyleSheet(product_button_style("secondary"))
        self._layout_timer.start(0)


class QuestionnaireReceipt(QWidget):
    """A read-only receipt; it never infers submitted answers from UI defaults."""

    def __init__(self, request, parent):
        super().__init__(parent)
        self.request = request
        self.rows = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.label = _label("回答已接收", self)
        self.view_btn = QPushButton("查看", self)
        self.view_btn.setCheckable(True)
        self.view_btn.setAccessibleName("查看已提交的回答")
        row.addWidget(self.label, 1)
        row.addWidget(self.view_btn)
        layout.addLayout(row)
        self.details = QWidget(self)
        self.details_layout = QVBoxLayout(self.details)
        self.details_layout.setContentsMargins(0, 0, 0, 0)
        self.details_layout.setSpacing(12)
        layout.addWidget(self.details)
        self.details.hide()
        self.view_btn.hide()
        self.view_btn.toggled.connect(self._toggle)
        self.refresh_theme()
        bind_theme(self, self.refresh_theme, surface="feedback")

    def _toggle(self, expanded):
        self.details.setVisible(expanded)
        self.view_btn.setText("收起" if expanded else "查看")

    def set_answers(self, answers, status="completed"):
        if answers is None:
            return
        for row in self.rows:
            self.details_layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        self.rows = []
        count = 0
        for question in normalize_pending_clarify_questions(self.request.get("questions") or []):
            answer = answers.get(question["id"])
            if not isinstance(answer, dict):
                continue
            options = {str(option.get("value") or option.get("label")): str(option.get("label") or option.get("value")) for option in question.get("options") or []}
            selected = [str(value) for value in answer.get("selected_options") or []]
            text = str(answer.get("text") or "").strip()
            value = text or "、".join(options.get(item, item) for item in selected if item != "__custom__")
            if not value:
                continue
            count += 1
            row = QWidget(self.details)
            column = QVBoxLayout(row)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(4)
            row.question_label = _label(question.get("header") or question["question"], row)
            row.answer_label = _label(value, row)
            for label in (row.question_label, row.answer_label):
                label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
                column.addWidget(label)
            self.details_layout.addWidget(row)
            self.rows.append(row)
        titles = {"auto_selected": f"系统自动选择了 {count} 项回答", "completed": f"已提交 {count} 项回答",
                  "cancelled": "已取消 · 回答未提交", "timeout": "请求已超时 · 回答未提交",
                  "error": "请求处理失败 · 回答未提交"}
        self.label.setText(titles.get(status, "请求已结束 · 回答未提交"))
        self.view_btn.setAccessibleName("查看已提交的回答" if status in {"completed", "auto_selected"} else "查看未提交的回答")
        self.view_btn.setVisible(bool(count))
        self.refresh_theme()

    def refresh_theme(self, _resolved=None):
        self.label.setStyleSheet(f"background: transparent; border: none; color: {DesignTokens.overlay_text}; font-size: {DesignTokens.font_size_body}px;")
        self.view_btn.setStyleSheet(product_button_style("ghost"))
        for row in self.rows:
            row.question_label.setStyleSheet(f"background: transparent; border: none; color: {DesignTokens.text_secondary}; font-size: {DesignTokens.font_size_meta}px;")
            row.answer_label.setStyleSheet(f"background: transparent; border: none; color: {DesignTokens.overlay_text}; font-size: {DesignTokens.font_size_body}px;")


def questionnaire_result(value):
    """Read the already-persisted tool result without adding messages or defaults."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    if not isinstance(value, dict):
        return None
    request, response = value.get("interaction_request"), value.get("interaction_response")
    if not isinstance(request, dict) or request.get("kind") != "questionnaire" or not isinstance(response, dict):
        return None
    if response.get("status") not in {"completed", "auto_selected"} or response.get("approved") is not True:
        return None
    if not isinstance(response.get("answers"), dict) or not response["answers"]:
        return None
    request = copy.deepcopy(request)
    request["request_id"] = str(response.get("request_id") or "")
    return request, response


class InlineInteractionCard(QFrame):
    """Conversation-owned response card for approval and input tool requests."""

    resolved = Signal(object)

    def __init__(self, request, parent=None):
        super().__init__(parent)
        self.request = dict(request or {})
        self.run_id = str((self.request.get("metadata") or {}).get("run_id") or "")
        self.kind = str(self.request.get("kind") or "approval").strip().lower()
        self._resolved = False
        self._terminal = False
        self._terminal_status = ""
        self._submitted_value = None
        self.questionnaire = None
        self.receipt = None
        self.option_checks = []
        self.question_controls = []
        self.question_labels = []
        self.option_group = None
        self.setObjectName("InlineInteractionCard")
        self.setStyleSheet(
            f"QFrame#InlineInteractionCard {{ background: {DesignTokens.bg_main}; "
            f"border: 1px solid {DesignTokens.border_subtle}; border-radius: 8px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        if self.kind == "questionnaire":
            self.questionnaire = QuestionnaireForm(self.request, self)
            layout.addWidget(self.questionnaire)
            for name in ("title_label", "message_label", "validation_label", "cancel_btn", "submit_btn", "question_controls"):
                setattr(self, name, getattr(self.questionnaire, name))
            self.question_labels = [section.title_label for section in self.questionnaire.sections]
            self.text_input = QLineEdit(self)
            self.text_input.hide()
            self.permission_details = self.permission_details_button = None
            self._validation_tone = "error"
            self.cancel_btn.clicked.connect(lambda: self._finish(False))
            self.submit_btn.clicked.connect(self._submit)
            self.receipt = QuestionnaireReceipt(self.request, self)
            self.receipt.hide()
            layout.addWidget(self.receipt)
            self.refresh_theme()
            bind_theme(self, self.refresh_theme, surface="feedback")
            return
        self.title_label = QLabel(str(self.request.get("title") or "需要你的输入"), self)
        self.title_label.setStyleSheet(f"color: {DesignTokens.text_primary}; font-size: 13px; font-weight: 700;")
        self.message_label = QLabel(str(self.request.get("message") or ""), self)
        self.message_label.setWordWrap(True)
        self.message_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.message_label.setStyleSheet(f"color: {DesignTokens.text_secondary}; font-size: 12px;")
        layout.addWidget(self.title_label)
        layout.addWidget(self.message_label)

        permission_meta = self.request.get("metadata") or {}
        self.permission_details = None
        self.permission_details_button = None
        if permission_meta.get("execution_permission"):
            self.message_label.setText("\n".join(filter(None, [self.message_label.text(), permission_meta.get("scope", "") ])))
            self.permission_details_button = QPushButton("查看执行详情", self)
            self.permission_details_button.setCheckable(True)
            self.permission_details_button.setStyleSheet(product_button_style("secondary"))
            layout.addWidget(self.permission_details_button)

            def show_permission_details(checked):
                if self.permission_details is None and checked:
                    self.permission_details = QPlainTextEdit(self)
                    self.permission_details.setReadOnly(True)
                    self.permission_details.setPlainText(str(permission_meta.get("details") or ""))
                    self.permission_details.setMinimumHeight(120)
                    self.permission_details.setMaximumHeight(260)
                    self.permission_details.setStyleSheet(product_field_style())
                    layout.insertWidget(layout.indexOf(self.permission_details_button) + 1, self.permission_details)
                if self.permission_details is not None:
                    self.permission_details.setVisible(checked)

            self.permission_details_button.toggled.connect(show_permission_details)

        self.text_input = QLineEdit(self)
        self.text_input.setPlaceholderText("自定义" if self.kind in {"choice", "multi_choice"} else "请输入回答")
        self.text_input.setStyleSheet(product_field_style())
        self.text_input.setVisible(
            self.kind == "text"
            or (self.kind in {"choice", "multi_choice"} and bool(self.request.get("allow_free_text")))
        )
        if self.kind == "text":
            layout.addWidget(self.text_input)

        options = self.request.get("options") if isinstance(self.request.get("options"), list) else []
        if self.kind in {"choice", "multi_choice"}:
            if self.kind == "choice":
                self.option_group = QButtonGroup(self)
                self.option_group.setExclusive(True)
            for option in options:
                label = str(option.get("label") or option.get("value") or "选项")
                if str(option.get("value") or "") == "__custom__" and self.request.get("allow_free_text"):
                    continue
                description = str(option.get("description") or "")
                choice = InteractionChoiceButton(label, description, str(option.get("value") or label), self,
                                                 multiple=self.kind == "multi_choice")
                if self.option_group is not None:
                    self.option_group.addButton(choice)
                self.option_checks.append(choice)
                layout.addWidget(choice)
                if self.kind == "choice":
                    choice.toggled.connect(lambda checked, field=self.text_input: field.clear() if checked else None)
            if not self.text_input.isHidden():
                layout.addWidget(self.text_input)
            if self.option_group is not None:
                self.text_input.textChanged.connect(lambda text: self._clear_choice_on_text(self.option_group, text))
        self.validation_label = QLabel("", self)
        self.validation_label.setWordWrap(True)
        self._validation_tone = "error"
        self.validation_label.setStyleSheet(f"color: {DesignTokens.error_text}; font-size: 11px;")
        self.validation_label.hide()
        layout.addWidget(self.validation_label)

        actions = QHBoxLayout()
        actions.addStretch()
        self.cancel_btn = QPushButton("取消", self)
        self.cancel_btn.setStyleSheet(product_button_style("secondary", radius=7))
        self.cancel_btn.clicked.connect(lambda: self._finish(False))
        self.submit_btn = QPushButton("继续" if self.kind == "approval" else "提交", self)
        if permission_meta.get("execution_permission"):
            self.cancel_btn.setText("拒绝")
            self.submit_btn.setText("允许本次执行")
        self.submit_btn.setStyleSheet(product_button_style("primary", radius=7))
        self.submit_btn.clicked.connect(self._submit)
        actions.addWidget(self.cancel_btn)
        actions.addWidget(self.submit_btn)
        layout.addLayout(actions)

        self.refresh_theme()
        bind_theme(self, self.refresh_theme, surface="feedback")

    @staticmethod
    def _clear_choice_on_text(group, text):
        if not text.strip():
            return
        group.setExclusive(False)
        for button in group.buttons():
            button.setChecked(False)
        group.setExclusive(True)

    def refresh_theme(self, _resolved=None):
        self.setStyleSheet(
            f"QFrame#InlineInteractionCard {{ background: {DesignTokens.overlay_bg}; "
            f"border: 1px solid {DesignTokens.overlay_border}; "
            f"border-radius: {DesignTokens.overlay_radius}px; }}"
        )
        if self.questionnaire is not None:
            self.questionnaire.refresh_theme()
            validation_color = DesignTokens.error_text if self._validation_tone == "error" else DesignTokens.text_secondary
            self.validation_label.setStyleSheet(f"background: transparent; border: none; color: {validation_color}; font-size: {DesignTokens.font_size_meta}px;")
            if self.receipt is not None and not self.receipt.isHidden():
                self.setStyleSheet(f"QFrame#InlineInteractionCard {{ background: {DesignTokens.overlay_bg}; border: none; }}")
            return
        self.title_label.setStyleSheet(
            f"color: {DesignTokens.overlay_text}; "
            f"font-size: {DesignTokens.font_size_body}px; "
            f"font-weight: {DesignTokens.font_weight_bold};"
        )
        self.message_label.setStyleSheet(
            f"color: {DesignTokens.text_secondary}; "
            f"font-size: {DesignTokens.font_size_meta}px;"
        )
        for label in self.question_labels:
            label.setStyleSheet(
                f"color: {DesignTokens.overlay_text}; "
                f"font-size: {DesignTokens.font_size_meta}px; "
                f"font-weight: {DesignTokens.font_weight_semibold};"
            )
        self.text_input.setStyleSheet(product_field_style())
        for control in self.question_controls:
            control["input"].setStyleSheet(product_field_style())
        if self.permission_details is not None:
            self.permission_details.setStyleSheet(product_field_style())
        if self.permission_details_button is not None:
            self.permission_details_button.setStyleSheet(product_button_style("secondary"))
        validation_color = (
            DesignTokens.success_text
            if self._validation_tone == "success"
            else DesignTokens.text_secondary if self._validation_tone == "info"
            else DesignTokens.error_text
        )
        self.validation_label.setStyleSheet(
            f"color: {validation_color}; "
            f"font-size: {DesignTokens.font_size_caption}px;"
        )
        self.cancel_btn.setStyleSheet(
            product_button_style("secondary", radius=DesignTokens.radius_sm)
        )
        self.submit_btn.setStyleSheet(
            product_button_style("primary", radius=DesignTokens.radius_sm)
        )

    def _select_only(self, current, checked):
        if not checked:
            return
        for checkbox in self.option_checks:
            if checkbox is not current:
                checkbox.setChecked(False)

    def _selected_values(self):
        return [
            str(checkbox.property("option_value") or "")
            for checkbox in self.option_checks
            if checkbox.isChecked() and str(checkbox.property("option_value") or "")
        ]

    def _show_validation(self, text):
        self._validation_tone = "error"
        self.validation_label.setText(str(text or "请检查输入"))
        self.refresh_theme()
        self.validation_label.show()

    def _submit(self):
        if self.kind == "approval":
            self._finish(True)
            return
        text = self.text_input.text().strip()
        picked = self._selected_values()
        if self.kind == "text":
            if not text:
                self._show_validation("请先输入内容。")
                return
            self._finish(text)
            return
        if self.kind == "choice":
            if picked:
                self._finish(picked[0])
                return
            if text:
                self._finish(text)
                return
            self._show_validation("请选择一个选项或输入补充内容。")
            return
        if self.kind == "multi_choice":
            values = list(picked)
            if text:
                values.append(text)
            if not values:
                self._show_validation("请至少选择一个选项或输入补充内容。")
                return
            self._finish(values)
            return
        if self.kind == "questionnaire":
            missing = self.questionnaire.missing_section()
            if missing is not None:
                missing.header.setChecked(True)
                self._show_validation("请选择一个选项或填写其他回答。")
                missing.input.setFocus(Qt.OtherFocusReason)
                self.questionnaire.scroll.ensureWidgetVisible(missing.input)
                return
            answers = self.questionnaire_answers()
            if not answers:
                self._show_validation("请至少回答一个问题。")
                return
            self._finish(answers)

    def questionnaire_answers(self):
        answers = {}
        for control in self.question_controls:
            checked = control["group"].checkedButton()
            value = str(checked.property("option_value") or "") if checked is not None else ""
            text = control["input"].text().strip()
            if value or text:
                answers[str(control["id"])] = {"selected_options": [value] if value else [],
                                               "text": text if not value else "", "raw_value": value or text}
        return answers

    def _set_controls_enabled(self, enabled):
        for widget in [self.text_input, self.cancel_btn, self.submit_btn, *self.option_checks]:
            widget.setEnabled(enabled)
        for control in self.question_controls:
            for button in control["buttons"]:
                button.setEnabled(enabled)
            control["input"].setEnabled(enabled)
            if "section" in control:
                control["section"].header.setEnabled(enabled)

    def _finish(self, value):
        if self._resolved:
            return
        self._resolved = True
        self._submitted_value = copy.deepcopy(value)
        self._set_controls_enabled(False)
        self._validation_tone = "info"
        self.refresh_theme()
        self.validation_label.setText("正在提交…")
        self.validation_label.show()
        if self.questionnaire is not None:
            self.submit_btn.setText("正在提交…")
            self.questionnaire.status_label.setText("正在提交…")
        self.resolved.emit(value)

    def restore_after_resolution_error(self, message):
        if self._terminal:
            return
        self._resolved = False
        self._set_controls_enabled(True)
        self._validation_tone = "error"
        self.refresh_theme()
        self.validation_label.setText(f"提交失败：{str(message or '未知错误')}。请重试。")
        self.validation_label.show()
        if self.questionnaire is not None:
            self.submit_btn.setText("重试提交")
            self.questionnaire.status_label.setText("待回答")

    def stop_waiting(self):
        self.finish_waiting("cancelled")

    def finish_waiting(self, status):
        self._terminal = True
        self._terminal_status = status
        self._resolved = True
        self._set_controls_enabled(False)
        if self.questionnaire is not None:
            self.show_receipt(None if status in {"completed", "auto_selected"} else self.questionnaire_answers(), status)
            return
        for field in [self.text_input, *(control["input"] for control in self.question_controls)]:
            field.setEnabled(True)
            field.setReadOnly(True)
        self.cancel_btn.hide()
        self.submit_btn.hide()
        messages = {
            "timeout": "请求已超时，未提交的输入保留在这里。",
            "cancelled": "任务已停止或请求已取消，未提交的输入保留在这里。",
            "error": "请求处理失败，未提交的输入保留在这里。",
        }
        self._validation_tone = "info"
        self.validation_label.setText(messages.get(status, "请求已结束，未提交的输入保留在这里。"))
        self.refresh_theme()
        self.validation_label.show()

    def show_receipt(self, answers, status="completed"):
        if self.receipt is None:
            return
        self._terminal = self._resolved = True
        self._terminal_status = status
        self._set_controls_enabled(False)
        self.questionnaire.hide()
        self.receipt.set_answers(answers, status)
        self.receipt.show()
        self.refresh_theme()
        self.updateGeometry()
