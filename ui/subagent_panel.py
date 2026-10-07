"""Compact child overview and an addressed conversation, sharing the host renderer."""
import copy
import json
import re
import uuid
import qtawesome as qta

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QStackedWidget, QPlainTextEdit,
    QSizePolicy,
)
from core.theme import DesignTokens, bind_theme
from core.conversation_render import build_conversation_render_items
from shiboken6 import isValid


STATUS = {"running": "运行中", "queued": "等待执行", "stopping": "正在停止",
          "interrupted": "已停止", "completed": "已完成", "failed": "失败",
          "failed_recovered": "上次运行已中断", "closed": "已关闭", "killed": "已关闭",
          "waiting_input": "有待处理消息"}


class _Reply(QObject):
    done = Signal(object)


class _Call(QRunnable):
    def __init__(self, call, reply):
        super().__init__()
        self.call, self.reply = call, reply

    def run(self):
        try:
            result = (self.call(), "")
        except Exception as exc:
            result = (None, str(exc))
        self.reply.done.emit(result)


class _TaskRow(QPushButton):
    """A keyboard-accessible button whose progress wraps at the drawer width."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.symbol = QLabel(self)
        self.label = QLabel(self)
        self.label.setWordWrap(True)
        self.label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        for child in (self.symbol, self.label):
            child.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.symbol, 0, Qt.AlignTop)
        layout.addWidget(self.label, 1)

    def setText(self, value):
        self.label.setText(value)
        self.setAccessibleName(value)

    def text(self):
        return self.label.text()

    def setIcon(self, icon):
        self.symbol.setPixmap(icon.pixmap(16, 16))


class SubagentPanel(QWidget):
    overview_changed = Signal(str, object)

    def __init__(self, render_item, parent=None):
        super().__init__(parent)
        self.render_item = render_item
        self.control = None
        self.conversation_id = ""
        self.agent_id = ""
        self.generation = 0
        self.views = {}
        self.rows = {}
        self.items = {}
        self.messages = {}
        self.summaries = {}
        self.inbox = []
        self.before = None
        self.events_cursor = 0
        self.events_run = ""
        self.event_tools = {}
        self.busy = set()
        self.replies = set()
        self.last_action_error = ""
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.notice = QLabel("尚未分配子任务。可在主对话中描述要并行完成的工作。", self)
        self.notice.setWordWrap(True)
        root.addWidget(self.notice)
        self.retry = QPushButton("重新加载", self)
        self.retry.clicked.connect(self.refresh)
        self.retry.hide()
        root.addWidget(self.retry)
        self.stack = QStackedWidget(self)
        root.addWidget(self.stack, 1)
        self.overview = QScrollArea(self.stack)
        self.overview.setWidgetResizable(True)
        self.list_content = QWidget(self.overview)
        self.list_layout = QVBoxLayout(self.list_content)
        self.list_layout.addStretch()
        self.overview.setWidget(self.list_content)
        self.stack.addWidget(self.overview)
        detail = QWidget(self.stack)
        layout = QVBoxLayout(detail)
        layout.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        back = QPushButton("返回子任务", detail)
        self.back = back
        self.detail = detail
        back.clicked.connect(self.show_overview)
        head.addWidget(back)
        self.title = QLabel(detail)
        self.title.setWordWrap(True)
        head.addWidget(self.title, 1)
        layout.addLayout(head)
        self.metrics = QLabel(detail)
        self.metrics.setWordWrap(True)
        layout.addWidget(self.metrics)
        self.older = QPushButton("加载更早消息", detail)
        self.older.clicked.connect(lambda: self.load_history(older=True))
        layout.addWidget(self.older)
        self.scroll = QScrollArea(detail)
        self.scroll.setWidgetResizable(True)
        self.content = QWidget(self.scroll)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(4, 4, 4, 4)
        self.content_layout.addStretch()
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)
        self.raw_toggle = QPushButton("查看运行详情", detail)
        self.raw_toggle.setCheckable(True)
        self.raw = QPlainTextEdit(detail)
        self.raw.setReadOnly(True)
        self.raw.setMaximumHeight(140)
        self.raw.hide()
        self.raw_toggle.toggled.connect(self.raw.setVisible)
        layout.addWidget(self.raw_toggle)
        layout.addWidget(self.raw)
        self.recipient = QLabel(detail)
        layout.addWidget(self.recipient)
        self.editor = QPlainTextEdit(detail)
        self.editor.setPlaceholderText("补充消息；运行中发送的内容会排队处理")
        self.editor.setMaximumHeight(110)
        self.recipient.setBuddy(self.editor)
        layout.addWidget(self.editor)
        actions = QHBoxLayout()
        self.stop = QPushButton("停止本轮", detail)
        self.resume = QPushButton("继续处理", detail)
        self.send = QPushButton("发送", detail)
        self.stop.clicked.connect(lambda: self.command("interrupt"))
        self.resume.clicked.connect(lambda: self.command("resume"))
        self.send.clicked.connect(lambda: self.command("send"))
        actions.addWidget(self.stop)
        actions.addWidget(self.resume)
        actions.addStretch()
        actions.addWidget(self.send)
        layout.addLayout(actions)
        self.stack.addWidget(detail)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.apply_theme()
        bind_theme(self, self.apply_theme, surface="conversation")

    def apply_theme(self, _resolved=None):
        for index, widget in enumerate((self, self.stack, self.detail, self.list_content, self.content)):
            widget.setObjectName(f"SubagentSurface{index}")
            widget.setAttribute(Qt.WA_StyledBackground, True)
            widget.setStyleSheet(f"#{widget.objectName()} {{background:{DesignTokens.bg_main}; color:{DesignTokens.text_primary};}}")
        for widget in (self.overview, self.scroll):
            widget.setStyleSheet(f"QScrollArea {{border: none; background:{DesignTokens.bg_main};}}")
        for editor in (self.editor, self.raw):
            editor.setStyleSheet(f"QPlainTextEdit {{background:{DesignTokens.bg_secondary}; color:{DesignTokens.text_primary}; border:1px solid {DesignTokens.border_subtle}; border-radius:6px; padding:6px;}}")
        for button in (*self.rows.values(), self.back, self.send, self.stop, self.resume, self.older, self.retry, self.raw_toggle):
            button.setStyleSheet(f"QPushButton {{color:{DesignTokens.text_primary}; background:{DesignTokens.bg_secondary}; border:1px solid {DesignTokens.border_subtle}; padding:8px; border-radius:6px;}} QPushButton:disabled {{color:{DesignTokens.text_tertiary};}} QPushButton:focus {{border-color:{DesignTokens.primary};}}")
        self.notice.setStyleSheet(f"color:{DesignTokens.text_secondary};")
        self.metrics.setStyleSheet(f"color:{DesignTokens.text_secondary};")
        for label in (self.title, self.recipient):
            label.setStyleSheet(f"color:{DesignTokens.text_primary};")
        for row in self.rows.values():
            row.label.setStyleSheet(f"color:{DesignTokens.text_primary}; background:transparent;")

    def bind(self, conversation_id, control):
        self.control = control
        if conversation_id == self.conversation_id:
            self.refresh()
            return
        self._save_view()
        self.conversation_id = conversation_id
        self.agent_id = ""
        self.generation += 1
        self.stack.setCurrentIndex(0)
        for row in self.rows.values():
            row.hide()
            row.deleteLater()
        self.rows.clear()
        self.summaries.clear()
        self.notice.setText("正在加载子任务…")
        self.refresh()

    def _request(self, operation, callback, settled=None, **data):
        if self.control is None or not self.conversation_id:
            return
        identity = (self.generation, self.conversation_id, self.agent_id)
        key = (identity, operation)
        if key in self.busy:
            return
        self.busy.add(key)
        reply = _Reply()
        self.replies.add(reply)
        control, conversation_id = self.control, self.conversation_id
        def completed(response):
            if not isValid(self):
                reply.deleteLater()
                return
            self.busy.discard(key)
            self.replies.discard(reply)
            reply.deleteLater()
            if settled is not None:
                settled(response)
            if identity != (self.generation, self.conversation_id, self.agent_id):
                return
            result, error = response
            if error:
                self.last_action_error = "操作未完成，输入和已保存内容已保留。" + error
                self.notice.setText(self.last_action_error)
                self.retry.show()
                self.send.setEnabled(True)
                return
            self.retry.hide()
            callback(result)
        reply.done.connect(completed)
        QThreadPool.globalInstance().start(_Call(lambda: control.request(conversation_id, operation, **data), reply))

    def refresh(self):
        if not self.conversation_id:
            return
        self._request("list", self.set_summaries)
        if self.agent_id and self.isVisible():
            self.load_history()

    def set_summaries(self, summaries):
        self.summaries = {s["id"]: s for s in summaries}
        ordered = sorted(summaries, key=lambda s: (s.get("status") not in {"running", "queued", "stopping"}, s.get("created_at") or 0, s["id"]))
        for index, summary in enumerate(ordered):
            agent_id = summary["id"]
            row = self.rows.get(agent_id)
            if row is None:
                row = _TaskRow(self.list_content)
                row.clicked.connect(lambda checked=False, aid=agent_id: self.open_agent(aid))
                self.rows[agent_id] = row
                self.apply_theme()
            name = summary.get("name") or "子任务"
            progress = summary.get("last_error") or summary.get("latest_progress") or summary.get("last_result") or ""
            text = f"{name}  ·  {self.status_text(summary)}\n{self.metric_text(summary)}"
            if progress:
                text += "\n" + progress.replace("\n", " ")[:100]
            if row.text() != text:
                row.setText(text)
            symbol = {"running": "play", "queued": "clock", "stopping": "pause",
                      "interrupted": "pause", "completed": "check", "failed": "exclamation-circle",
                      "closed": "lock", "killed": "lock"}.get(summary.get("status"), "info-circle")
            row.setIcon(qta.icon("fa5s." + symbol, color=DesignTokens.text_secondary))
            self.list_layout.removeWidget(row)
            self.list_layout.insertWidget(index, row)
            row.show()
        self.overview_changed.emit(self.conversation_id, summaries)
        if not self.agent_id:
            self.notice.setText("选择子任务查看过程、补充消息或停止本轮。" if summaries else "尚未分配子任务。可在主对话中描述要并行完成的工作。")
        elif self.agent_id in self.summaries:
            self._set_summary(self.summaries[self.agent_id])

    @staticmethod
    def status_text(summary):
        text = STATUS.get(summary.get("status"), "状态未知")
        if summary.get("pending_count"):
            text += f" · {summary['pending_count']} 条待处理"
        return text

    @staticmethod
    def metric_text(summary):
        duration = summary.get("duration")
        usage = summary.get("usage")
        duration_text = f"本轮 {int(duration)} 秒" if duration is not None else "耗时暂无记录"
        token_text = f"{usage.get('input_tokens', 0) + usage.get('output_tokens', 0):,} tokens" if usage is not None else "用量暂无记录"
        return duration_text + " · " + token_text

    def _save_view(self):
        if not self.agent_id:
            return
        key = (self.conversation_id, self.agent_id)
        prior = self.views.get(key, {})
        self.views[key] = dict(prior, draft=self.editor.toPlainText(), scroll=self.scroll.verticalScrollBar().value(), raw=self.raw_toggle.isChecked(), items=self.items, messages=self.messages, before=self.before)

    def show_overview(self):
        self._save_view()
        self.agent_id = ""
        self.generation += 1
        self.stack.setCurrentIndex(0)
        self.refresh()

    def open_agent(self, agent_id):
        self._save_view()
        self.agent_id = agent_id
        self.last_action_error = ""
        self.generation += 1
        self.messages = {}
        self.before = None
        self.events_cursor = 0
        self.events_run = ""
        self.event_tools = {}
        for widget, _ in self.items.values():
            self.content_layout.removeWidget(widget)
            widget.hide()
        self.items = {}
        self.raw.clear()
        view = self.views.get((self.conversation_id, agent_id), {})
        self.items = view.get("items", {})
        self.messages = view.get("messages", {})
        self.before = view.get("before")
        self.editor.setPlainText(view.get("draft", ""))
        self.raw_toggle.setChecked(view.get("raw", False))
        self._restore_scroll = view.get("scroll")
        self.stack.setCurrentIndex(1)
        self.notice.setText("正在加载子会话…")
        self._set_summary(self.summaries.get(agent_id, {}))
        self.load_history()

    def _set_summary(self, summary):
        name = summary.get("name") or "子任务"
        self.title.setText("当前会话 / " + name)
        self.recipient.setText("发送给：" + name)
        self.metrics.setText(self.status_text(summary) + " · " + self.metric_text(summary))
        if summary.get("total_duration") is not None:
            self.metrics.setText(self.metrics.text() + f" · 累计 {int(summary['total_duration'])} 秒")
        closed = summary.get("status") in {"closed", "killed"}
        unavailable = bool(summary.get("control_error"))
        self.editor.setEnabled(not closed and not unavailable)
        self.send.setEnabled(not closed and not unavailable)
        self.stop.setEnabled(summary.get("status") == "running" and not unavailable)
        self.resume.setVisible(bool(summary.get("paused") and summary.get("pending_count")))
        self.resume.setEnabled(summary.get("status") != "stopping" and not unavailable)
        self.notice.setText(self.last_action_error or summary.get("control_error") or ("此子任务已关闭，历史只读。" if closed else summary.get("last_error") or ("已停止；排队消息已保留。" if summary.get("paused") else "运行中发送的消息将按顺序处理。")))

    def load_history(self, older=False):
        if not self.agent_id:
            return
        def loaded(page):
            self._prepending = older
            for message in page["messages"]:
                self.messages[message["id"]] = message
            # Dictionary insertion order follows the stable SQLite positions; earlier pages prepend.
            if older:
                head = {m["id"]: m for m in page["messages"]}
                self.messages = dict(head, **self.messages)
            if older or self.before is None:
                self.before = page["before"]
            self.older.setVisible(self.before is not None)
            self.inbox = page["inbox"]
            for event in page.get("activity", []):
                payload = event.get("payload") or {}
                tool_key = payload.get("run_id", "") + ":" + payload.get("tool_call_id", "")
                self.event_tools[tool_key] = dict(self.event_tools.get(tool_key, {}), **payload)
            self.summaries[self.agent_id] = page["summary"]
            self._set_summary(page["summary"])
            self._render()
            run_id = page["summary"].get("run_id", "")
            if run_id != self.events_run:
                self.events_run, self.events_cursor = run_id, 0
            if not page["summary"].get("read_only"):
                self._request("events", self._events, agent_id=self.agent_id, run_id=run_id, after=self.events_cursor)
        self._request("history", loaded, agent_id=self.agent_id, before=self.before if older else None)

    def _events(self, result):
        for event in result["events"]:
            self.events_cursor = event["sequence"]
            payload = event.get("payload") or {}
            status = payload.get("status")
            self.raw.appendPlainText(json.dumps(payload, ensure_ascii=False))
        self._render()

    def _render(self):
        bar = self.scroll.verticalScrollBar()
        previous, maximum = bar.value(), bar.maximum()
        follow = maximum - previous < 24
        messages = copy.deepcopy(list(self.messages.values()))
        # A run's reserved answer remains one growing transcript block, before later queued input.
        grouped = {}
        for message in messages:
            mid = message["id"]
            if re.fullmatch(r"[0-9a-f]{32}:(message:\d+|assistant)", mid):
                grouped.setdefault(mid.split(":")[0], []).append(message)
        ordered, emitted = [], set()
        for message in messages:
            run = message["id"].split(":")[0]
            if run not in grouped:
                ordered.append(message)
            elif run not in emitted:
                emitted.add(run)
                ordered.extend(sorted(grouped[run], key=lambda m: int(m["id"].rsplit(":", 1)[1]) if ":message:" in m["id"] else 10**9))
        messages = ordered
        # Tool ids may repeat across runs; namespace the presentation only.
        for msg in messages:
            scope = msg["id"].split(":")[0]
            for call in msg.get("tool_calls") or []:
                call["id"] = scope + ":" + call["id"]
            if msg.get("tool_call_id"):
                msg["tool_call_id"] = scope + ":" + msg["tool_call_id"]
        rendered = build_conversation_render_items(messages)
        known = {tool["id"] for item in rendered for tool in item.get("tool_calls", [])}
        for key, event in self.event_tools.items():
            if key not in known:
                rendered.append({"type": "assistant", "messages": [{"id": "activity:" + key}],
                    "content": "", "reasoning": "", "tool_calls": [{"id": key,
                    "name": event.get("tool_name", "工具"), "args": event.get("tool_args", {}),
                    "result": event.get("tool_result", ""), "result_obj": event.get("tool_result_obj")} ]})
        visible_keys = set()
        for index, item in enumerate(rendered):
            source = item.get("messages") or [item.get("message") or {}]
            key = source[0].get("id")
            if not key:
                continue
            item["delivery_state"] = next((r["state"] for r in self.inbox if r["message_id"] == key), "")
            visible_keys.add(key)
            signature = json.dumps(item, ensure_ascii=False, sort_keys=True)
            widget, old = self.items.get(key, (None, None))
            if old != signature:
                widget = self.render_item(item, self.content, widget)
                self.items[key] = (widget, signature)
            self.content_layout.removeWidget(widget)
            self.content_layout.insertWidget(index, widget)
            if hasattr(widget, "apply_dynamic_widths"):
                width = max(160, self.scroll.viewport().width() - 16)
                widget.apply_dynamic_widths(width, max(140, width - 16))
            widget.show()
        for key, (widget, _) in self.items.items():
            if key not in visible_keys:
                self.content_layout.removeWidget(widget)
                widget.hide()
        desired = getattr(self, "_restore_scroll", None)
        self._restore_scroll = None
        prepend = getattr(self, "_prepending", False)
        self._prepending = False
        identity = (self.generation, self.conversation_id, self.agent_id)
        def restore():
            if not isValid(self) or identity != (self.generation, self.conversation_id, self.agent_id):
                return
            bar.setValue(desired if desired is not None else previous + max(0, bar.maximum() - maximum)
                         if prepend else bar.maximum() if follow else previous)
        QTimer.singleShot(0, restore)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.agent_id:
            self._render()

    def command(self, operation):
        identity = (self.generation, self.conversation_id, self.agent_id)
        if (identity, operation) in self.busy:
            return
        summary = self.summaries.get(self.agent_id, {})
        data = {"agent_id": self.agent_id, "run_id": summary.get("run_id", "")}
        key = (self.conversation_id, self.agent_id)
        text = self.editor.toPlainText().strip()
        if operation == "send":
            if not text:
                return
            view = self.views.setdefault(key, {})
            if view.get("pending_text") != text:
                view.update(pending_text=text, message_id=uuid.uuid4().hex)
            data.update(content=text, message_id=view["message_id"])
            self.send.setEnabled(False)
        def done(result):
            self.last_action_error = ""
            if operation == "send":
                if self.editor.toPlainText().strip() == text:
                    self.editor.clear()
                self.views.setdefault(key, {}).pop("pending_text", None)
                self.notice.setText("已接收并保存；待处理消息按顺序执行。")
            self.refresh()
        def settled(response):
            result, error = response
            if not error and operation == "send":
                view = self.views.setdefault(key, {})
                if view.get("draft", "").strip() == text:
                    view["draft"] = ""
                if view.get("pending_text") == text:
                    view.pop("pending_text", None)
        self._request(operation, done, settled=settled, **data)

    # Compatibility with existing drawer clearing and event coalescing entrypoints.
    def reset(self):
        self._save_view()

    def set_notice(self, text):
        self.notice.setText(text)

    def update_log(self, *args, **kwargs):
        self.refresh()
