"""Bounded, user-facing security activity view; no detection or storage imports."""
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox, QPlainTextEdit
from ui.primitives import product_button_style, product_code_style

RULE_NAMES = {
    "remote_execution": "下载内容后直接执行",
    "credential_access": "可能访问凭证文件",
    "instruction_override": "可能要求忽略原有指令",
    "encoded_execution": "可能执行编码后的内容",
}


def needs_attention(event):
    return bool(event.get("findings") or event.get("event") == "credential"
                or event.get("status") in ("partial", "failed"))


def describe(event):
    kind, status = event.get("event"), event.get("status")
    if kind in ("skill", "mcp"):
        title = "Skill 检查" if kind == "skill" else "MCP 检查"
        if status == "running":
            return title + "进行中", "检查期间仍可正常使用。"
        if status == "failed":
            return title + "未完成", "可稍后重新检查，当前使用不受影响。"
        if event.get("findings"):
            return title + "发现风险线索", ("部分内容未检查。" if status == "partial" else "") + "建议核对来源和用途；发现线索不代表有害。"
        if status == "partial":
            return title + "未完成", "部分内容未检查，请查看详情了解范围。"
        if status == "complete":
            return title + "未发现明显风险", "仅检查了可读取的文本，不代表全部内容安全。"
    if kind == "credential" and status == "complete":
        return "发现疑似密钥或密码", "建议确认是否需要发送；内容可能已发送，本次未阻断。"
    if kind == "tokenize" and status == "complete":
        return "已隐藏选中的敏感信息", "仅处理发给模型的副本，原始消息和文件未修改。"
    title = {"tokenize": "敏感信息处理", "credential": "凭证提醒", "scan": "安全检查",
             "log": "记录保存"}.get(kind, "数据安全")
    if status in ("partial", "failed"):
        title += "未完成"
    return title, event.get("message") or "请查看详情。"


class SecurityRecords(QWidget):
    PAGE_SIZE = 5
    LIMIT = 60

    def __init__(self, parent=None):
        super().__init__(parent)
        self.records = []
        self.page = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.filter = QCheckBox("只看需要关注的记录")
        self.filter.toggled.connect(self.reset_page)
        layout.addWidget(self.filter)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.rows = QVBoxLayout()
        layout.addLayout(self.rows)
        navigation = QHBoxLayout()
        self.previous = QPushButton("上一页")
        self.next = QPushButton("下一页")
        self.page_label = QLabel()
        self.previous.clicked.connect(lambda: self.move_page(-1))
        self.next.clicked.connect(lambda: self.move_page(1))
        navigation.addWidget(self.previous)
        navigation.addWidget(self.page_label)
        navigation.addWidget(self.next)
        navigation.addStretch()
        layout.addLayout(navigation)
        self.render()

    def set_records(self, events):
        # Merge disk/memory copies without hiding separate runs with the same outcome.
        unique = []
        seen = {}
        for event in sorted(events, key=lambda e: e.get("timestamp", 0), reverse=True):
            key = tuple(str(event.get(k, "")) for k in ("event", "run_id", "object_id", "status", "message"))
            stamp = event.get("timestamp", 0)
            if key in seen and abs(seen[key] - stamp) < 1:
                continue
            seen[key] = stamp
            unique.append(event)
        self.records = unique[:self.LIMIT]
        self.reset_page()

    def reset_page(self):
        self.page = 0
        self.render()

    def move_page(self, delta):
        self.page += delta
        self.render()

    def render(self):
        while self.rows.count():
            widget = self.rows.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        records = [e for e in self.records if not self.filter.isChecked() or needs_attention(e)]
        pages = max(1, (len(records) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self.page = min(max(0, self.page), pages - 1)
        count = sum(needs_attention(e) for e in self.records)
        self.summary.setText(f"最近 {len(self.records)} 条 · {count} 条需要关注。最多显示最近 60 条，按时间从新到旧排列。"
                             if self.records else "暂无记录。启用并保存相应功能后，发送消息或安装、更新 Skill 等操作会产生记录。")
        if not records and self.records:
            self.rows.addWidget(QLabel("最近记录中没有需要关注的项目。"))
        for event in records[self.page * self.PAGE_SIZE:(self.page + 1) * self.PAGE_SIZE]:
            self.rows.addWidget(self.make_row(event))
        self.page_label.setText(f"{self.page + 1} / {pages}")
        self.previous.setEnabled(self.page > 0)
        self.next.setEnabled(self.page + 1 < pages)
        for button in (self.previous, self.next):
            button.setStyleSheet(product_button_style("secondary"))

    def make_row(self, event):
        row = QWidget()
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 8, 0, 8)
        title, description = describe(event)
        try:
            stamp = datetime.fromtimestamp(event.get("timestamp", 0)).strftime("%m-%d %H:%M")
        except (ValueError, OSError, OverflowError):
            stamp = "时间未知"
        for text in (f"{stamp} · {title}", event.get("object_id"), description):
            if text:
                text = str(text)
                label = QLabel(text if len(text) <= 180 else text[:180] + "…")
                label.setTextFormat(Qt.TextFormat.PlainText)
                label.setWordWrap(True)
                label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                layout.addWidget(label)
        toggle = QPushButton("查看详情")
        toggle.setCheckable(True)
        toggle.setStyleSheet(product_button_style("ghost"))
        layout.addWidget(toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        details = QPlainTextEdit()
        details.setReadOnly(True)
        details.setFixedHeight(160)
        details.setStyleSheet(product_code_style())
        details.hide()
        layout.addWidget(details)

        def expand(checked):
            if checked and not details.toPlainText():
                lines = [event.get("message", description)]
                if event.get("object_id"):
                    lines.insert(0, "检查对象：" + str(event["object_id"]))
                if event.get("coverage"):
                    lines.append("检查范围：" + event["coverage"])
                if event.get("skipped"):
                    lines.append(f"未检查部分：{event['skipped']} 项")
                for finding in event.get("findings", [])[:200]:
                    lines.append(f"{RULE_NAMES.get(finding.get('rule'), '需核对的风险线索')}\n  {finding.get('file', '')} · 第 {finding.get('line', '?')} 行")
                if event.get("error_type"):
                    lines.append("技术原因（供排查）：" + event["error_type"])
                details.setPlainText("\n".join(lines))
            details.setVisible(checked)
            toggle.setText("收起详情" if checked else "查看详情")
        toggle.toggled.connect(expand)
        return row
