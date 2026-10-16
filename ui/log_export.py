"""Read-only runtime-log page and atomic TXT export, backed by one inventory."""

import os
import platform
import re
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QIODevice, QSaveFile, QStandardPaths, QThread, QTimer, Qt, Slot
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QBoxLayout, QWidget, QLineEdit, QPlainTextEdit,
)

from core.app_version import APP_VERSION
from core.runtime_logs import (
    PAGE_SIZE, SOURCE_LABELS, collect_files, date_range, iter_records, local_now,
    maintenance_issues, query_logs, selected_files, validate_export_target,
)
from core.task_details import redact_diagnostic
from core.theme import DesignTokens, bind_theme
from ui.primitives import (
    PageScrollSafeComboBox, ProductInlineNotice, product_button_style,
    product_code_style, product_field_style,
)
from ui.worker_lifecycle import ui_work_stopping


PREVIEW_CHAR_LIMIT = 1024 * 1024
RECORD_PREVIEW_CHAR_LIMIT = 64 * 1024


class LogExportWorker(QThread):
    def __init__(self, source_dir, target, audit, parent, *, days=1, source=""):
        super().__init__(parent)
        self.source_dir = Path(source_dir)
        self.target = target
        self.audit = audit
        self.days = days
        self.source = source
        self.error = ""
        self.warnings = []
        self.count = 0
        self.record_count = 0

    @Slot()
    def shutdown(self):
        self.requestInterruption()
        self.wait()

    def run(self):
        output = None
        try:
            now = local_now()
            start, end = date_range(self.days, now)
            self.audit("log_export.log", f"stage=start days={self.days} source={self.source or 'all'}")
            files, self.warnings = collect_files(self.source_dir)
            validate_export_target(self.target, self.source_dir, files)
            selected = selected_files(files, self.days, self.source, now)
            output = QSaveFile(self.target)
            if not output.open(QIODevice.WriteOnly):
                raise OSError(output.errorString())

            def write(text):
                data = text.encode("utf-8")
                if output.write(data) != len(data):
                    raise OSError(output.errorString())

            write(
                f"DeepSeek Cowork 运行日志\n应用版本：{APP_VERSION}\n系统：{platform.platform()}\n"
                f"导出时间：{now.isoformat(timespec='seconds')}\n时间范围：{start} 至 {end}（本地日期）\n"
                f"来源：{SOURCE_LABELS.get(self.source, '全部来源')}\n"
                "包含所选时间与来源的完整运行日志，不按关键词或预览条数截断。\n"
                "各文件按读取开始时的字节数快照；运行中的最后一条记录可能尚未写完。\n"
                "不包含配置文件、聊天数据库、恢复数据或更新备份。\n"
                "日志可能包含本地路径及任务内容；已应用现有诊断脱敏规则。\n\n来源文件清单：\n"
            )
            for file in selected:
                write(f"- {SOURCE_LABELS[file.source]} | {file.path} | 快照大小 {file.size} 字节\n")
            for file in selected:
                if self.isInterruptionRequested():
                    raise InterruptedError("日志导出已取消。")
                wrote_header = False
                reported_decode = False
                # Only source-read failures permit a partial export. Destination
                # write failures must cancel QSaveFile instead of pretending success.
                records = iter_records(file, self.isInterruptionRequested)
                while True:
                    try:
                        record = next(records)
                    except StopIteration:
                        break
                    except OSError as exc:
                        if isinstance(exc, InterruptedError):
                            raise
                        self.warnings.append(f"无法完整读取 {file.path}：{exc}")
                        break
                    if not start <= record.timestamp.date() <= end:
                        continue
                    if record.decode_errors and not reported_decode:
                        self.warnings.append(f"{file.path} 包含无法解码的字节，已用替代字符显示；原文件未改动。")
                        reported_decode = True
                    if not wrote_header:
                        write(f"\n{'=' * 64}\n来源：{SOURCE_LABELS[file.source]}\n日志文件：{file.path}\n\n")
                        wrote_header = True
                        self.count += 1
                    write(record.display_text().rstrip("\n") + "\n")
                    self.record_count += 1
            if self.warnings:
                write("\n[导出不完整：以下日志未能完整读取]\n" + "\n".join(redact_diagnostic(self.warnings)) + "\n")
            elif not self.record_count:
                raise ValueError("所选时间与来源暂无日志，可调整范围后重试。")
            write(f"\n导出结果：{self.count} 个文件，{self.record_count} 条记录。"
                  f"{'存在读取失败，请参见上文。' if self.warnings else '所选范围读取完成。'}\n")
            if self.isInterruptionRequested():
                raise InterruptedError("日志导出已取消。")
            if not output.commit():
                raise OSError(output.errorString())
            self.audit("log_export.log", f"stage={'partial' if self.warnings else 'completed'} files={self.count} records={self.record_count} warnings={len(self.warnings)}")
        except Exception as exc:
            if output is not None:
                output.cancelWriting()
            self.error = str(exc)
            self.audit("log_export.log", f"stage=failed error_type={type(exc).__name__}")


def _preview(records):
    blocks, size, shortened = [], 0, False
    before = None
    for record in reversed(records):
        text = record.display_text()
        # Time/source already appear in the readable heading. Exports retain
        # the original line, including its full metadata prefix.
        text = re.sub(r"^\[\d[^\]]+\]\s+(?:\[source=[^\]]+\]\s+)?", "", text, count=1)
        if len(text) > RECORD_PREVIEW_CHAR_LIMIT:
            half = RECORD_PREVIEW_CHAR_LIMIT // 2
            text = text[:half] + "\n[此条预览已截短，导出包含完整内容]\n" + text[-half:]
            shortened = True
        block = (f"[{record.timestamp.isoformat(timespec='milliseconds')}] {SOURCE_LABELS[record.file.source]}"
                 f" · {record.file.path.name}\n{text.rstrip()}\n")
        if size + len(block) > PREVIEW_CHAR_LIMIT:
            shortened = True
            break
        blocks.append(block)
        size += len(block)
        before = record.sort_key()
    return "\n".join(reversed(blocks)), shortened, len(blocks), before


class LogReadWorker(QThread):
    def __init__(self, source_dir, request_id, options, parent, audit=None):
        super().__init__(parent)
        self.source_dir = source_dir
        self.request_id = request_id
        self.options = options
        self.audit = audit
        self.result = None
        self.text = ""
        self.shortened = False
        self.displayed_count = 0
        self.before = None
        self.error = ""

    @Slot()
    def shutdown(self):
        self.requestInterruption()
        self.wait()

    def run(self):
        try:
            if self.audit:
                self.audit("log_export.log", f"stage=read_start request={self.request_id} days={self.options['days']} source={self.options['source'] or 'all'}")
            self.result = query_logs(self.source_dir, **self.options, cancelled=self.isInterruptionRequested)
            self.result.issues.extend(maintenance_issues(self.source_dir))
            self.text, self.shortened, self.displayed_count, self.before = _preview(self.result.records)
            if self.result.issues:
                self.text = "[读取与维护提示]\n" + "\n".join(redact_diagnostic(self.result.issues)) + "\n\n" + self.text
            if self.audit:
                self.audit("log_export.log", f"stage=read_completed request={self.request_id} records={self.result.total} issues={len(self.result.issues)}")
        except Exception as exc:
            self.error = str(exc)
            if self.audit:
                self.audit("log_export.log", f"stage={'read_cancelled' if isinstance(exc, InterruptedError) else 'read_failed'} request={self.request_id} error_type={type(exc).__name__}")


class LogExportPanel(QWidget):
    def __init__(self, source_dir, audit, parent=None):
        super().__init__(parent)
        self.setObjectName("RuntimeLogsPanel")
        self.source_dir = source_dir
        self.audit = audit
        self.worker = None
        self._read_worker = None
        self._pending_query = None
        self._query_id = 0
        self._before = None
        self._next_before = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.description = QLabel(
            "自动保留最近 30 天的运行日志。导出包含所选时间与来源的完整记录，关键词只用于查看。"
            "日志可能包含本地路径及任务内容。", self
        )
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        self.filters = QHBoxLayout()
        range_label = QLabel("时间范围", self)
        self.range_label = range_label
        self.range_combo = PageScrollSafeComboBox(self)
        self.range_combo.setAccessibleName("日志时间范围")
        for label, days in (("当天", 1), ("最近 7 天", 7), ("最近 30 天", 30)):
            self.range_combo.addItem(label, days)
        range_label.setBuddy(self.range_combo)
        source_label = QLabel("来源", self)
        self.source_label = source_label
        self.source_combo = PageScrollSafeComboBox(self)
        self.source_combo.setAccessibleName("日志来源")
        self.source_combo.addItem("全部来源", "")
        for source, label in SOURCE_LABELS.items():
            self.source_combo.addItem(label, source)
        source_label.setBuddy(self.source_combo)
        for label, combo in ((range_label, self.range_combo), (source_label, self.source_combo)):
            pair = QHBoxLayout()
            pair.addWidget(label)
            pair.addWidget(combo, 1)
            self.filters.addLayout(pair, 1)
        layout.addLayout(self.filters)
        search = QHBoxLayout()
        self.keyword = QLineEdit(self)
        self.keyword.setAccessibleName("日志关键词")
        self.keyword.setPlaceholderText("搜索关键词、会话或运行 ID，按 Enter 搜索")
        self.keyword.setClearButtonEnabled(True)
        self.search_button = QPushButton("搜索", self)
        search.addWidget(self.keyword, 1)
        search.addWidget(self.search_button)
        layout.addLayout(search)
        self.summary = QLabel("选择范围后可查看运行日志。", self)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.read_notice = ProductInlineNotice(parent=self)
        self.read_notice.hide()
        layout.addWidget(self.read_notice)
        self.viewer = QPlainTextEdit(self)
        self.viewer.setObjectName("RuntimeLogViewer")
        self.viewer.setAccessibleName("运行日志内容")
        self.viewer.setReadOnly(True)
        self.viewer.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.viewer.setMinimumHeight(160)
        layout.addWidget(self.viewer, 1)
        self.actions = QHBoxLayout()
        self.refresh_button = QPushButton("刷新", self)
        self.more_button = QPushButton("加载更早记录", self)
        self.more_button.setEnabled(False)
        self.button = QPushButton("导出日志…", self)
        self.actions.addWidget(self.refresh_button)
        self.actions.addWidget(self.more_button)
        self.actions.addStretch()
        self.actions.addWidget(self.button)
        layout.addLayout(self.actions)
        self.notice = ProductInlineNotice(parent=self)
        self.notice.label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.notice.actionRequested.connect(self.cancel_export)
        self.notice.hide()
        layout.addWidget(self.notice)
        self.range_combo.currentIndexChanged.connect(self.refresh_logs)
        self.source_combo.currentIndexChanged.connect(self.refresh_logs)
        self.keyword.returnPressed.connect(self.refresh_logs)
        self.search_button.clicked.connect(self.refresh_logs)
        self.refresh_button.clicked.connect(self.refresh_logs)
        self.more_button.clicked.connect(self.load_more)
        self.button.clicked.connect(self.export_logs)
        self.refresh_theme()
        bind_theme(self, self.refresh_theme)

    def refresh_theme(self, _resolved=None):
        self.description.setStyleSheet(f"color: {DesignTokens.text_secondary};")
        self.summary.setStyleSheet(f"color: {DesignTokens.text_secondary};")
        for label in (self.range_label, self.source_label):
            label.setStyleSheet(f"color: {DesignTokens.text_primary};")
        self.viewer.setStyleSheet(product_code_style("QPlainTextEdit#RuntimeLogViewer") +
            f"QPlainTextEdit#RuntimeLogViewer QScrollBar:horizontal {{ background: {DesignTokens.bg_code}; height: 12px; }}"
            f"QPlainTextEdit#RuntimeLogViewer QScrollBar::handle:horizontal {{ background: {DesignTokens.scrollbar_thumb}; min-width: {DesignTokens.control_height}px; border-radius: {DesignTokens.radius_sm}px; }}"
            f"QPlainTextEdit#RuntimeLogViewer QScrollBar::handle:horizontal:hover {{ background: {DesignTokens.scrollbar_thumb_hover}; }}"
            "QPlainTextEdit#RuntimeLogViewer QScrollBar::add-line:horizontal, QPlainTextEdit#RuntimeLogViewer QScrollBar::sub-line:horizontal { width: 0; }"
            "QPlainTextEdit#RuntimeLogViewer QScrollBar::add-page:horizontal, QPlainTextEdit#RuntimeLogViewer QScrollBar::sub-page:horizontal { background: transparent; }")
        for widget in (self.range_combo, self.source_combo, self.keyword):
            widget.setStyleSheet(product_field_style())
        for combo in (self.range_combo, self.source_combo):
            combo.setStyleSheet(product_field_style() + "QComboBox::drop-down { border: none; }")
            combo.view().setStyleSheet(
                f"QAbstractItemView {{ background: {DesignTokens.bg_main}; color: {DesignTokens.text_primary}; border: 1px solid {DesignTokens.border_subtle}; }}"
                f"QAbstractItemView::item {{ min-height: {DesignTokens.control_height}px; }}"
                f"QAbstractItemView::item:selected {{ background: {DesignTokens.primary_soft}; color: {DesignTokens.text_primary}; }}")
        for button in (self.refresh_button, self.more_button, self.search_button):
            button.setStyleSheet(product_button_style("secondary"))
        self.button.setStyleSheet(product_button_style("primary"))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.filters.setDirection(QBoxLayout.TopToBottom if self.width() < 440 else QBoxLayout.LeftToRight)
        self.actions.setDirection(QBoxLayout.TopToBottom if self.width() < 350 else QBoxLayout.LeftToRight)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.refresh_logs)

    def set_source(self, source):
        self.keyword.clear()
        index = self.source_combo.findData(source)
        if index >= 0:
            if index == self.source_combo.currentIndex():
                self.refresh_logs()
            else:
                self.source_combo.setCurrentIndex(index)

    @Slot()
    def refresh_logs(self):
        self._before = None
        self._request_read()

    @Slot()
    def load_more(self):
        if self._next_before is None:
            return
        self._before = self._next_before
        self._request_read()

    def _request_read(self):
        if self._work_stopping():
            return
        self._query_id += 1
        self._pending_query = (self._query_id, {
            "days": self.range_combo.currentData(), "source": self.source_combo.currentData(),
            "keyword": self.keyword.text().strip(), "limit": PAGE_SIZE, "before": self._before,
        })
        self.summary.setText("正在读取所选范围的日志…")
        self.more_button.setEnabled(False)
        self.read_notice.hide()
        self.viewer.clear()
        if self._read_worker is not None:
            self._read_worker.requestInterruption()
            return
        self._start_pending_read()

    def _start_pending_read(self):
        if self._work_stopping():
            self._pending_query = None
            return
        if self._pending_query is None:
            return
        request_id, options = self._pending_query
        self._pending_query = None
        app = QApplication.instance()
        worker = LogReadWorker(self.source_dir, request_id, options, app, self.audit)
        self._read_worker = worker
        app.aboutToQuit.connect(worker.shutdown)
        worker.finished.connect(self._read_finished)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    @Slot()
    def _read_finished(self):
        worker = self._read_worker
        self._read_worker = None
        if worker.request_id == self._query_id:
            if worker.error:
                self.summary.setText("日志读取失败，可点击刷新重试。")
                self.read_notice.set_text(redact_diagnostic(worker.error), "error")
                self.read_notice.show()
            else:
                result = worker.result
                if not result.records:
                    self.viewer.setPlaceholderText("所选范围暂无匹配日志。可切换时间、来源或清除关键词后搜索。")
                self.viewer.setPlainText(worker.text)
                self.viewer.verticalScrollBar().setValue(self.viewer.verticalScrollBar().maximum())
                self._next_before = worker.before
                position = "更早" if worker.options["before"] is not None else "最近"
                self.summary.setText(f"已显示{position} {worker.displayed_count:,} 条，共匹配 {result.total:,} 条"
                                     f" · {datetime.now():%H:%M:%S} 读取")
                self.more_button.setEnabled(result.remaining > worker.displayed_count)
                notes = []
                if result.issues:
                    notes.append(f"有 {len(result.issues)} 项读取或维护提示，详情在日志内容开头。")
                if worker.shortened:
                    notes.append("部分内容过长，预览已截短；导出包含完整内容。")
                if notes:
                    self.read_notice.set_text("\n".join(notes), "warning")
                    self.read_notice.show()
        self._start_pending_read()

    @Slot()
    def export_logs(self):
        if self.worker is not None or self._work_stopping():
            return
        directory = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation)
        filename = f"cowork-logs-{datetime.now():%Y%m%d-%H%M%S}.txt"
        dialog = QFileDialog(self, "导出运行日志", os.path.join(directory, filename))
        dialog.setAcceptMode(QFileDialog.AcceptSave)
        dialog.setNameFilter("文本文件 (*.txt)")
        dialog.setDefaultSuffix("txt")
        if not dialog.exec():
            dialog.deleteLater()
            return
        target = dialog.selectedFiles()[0]
        dialog.deleteLater()
        self.button.setEnabled(False)
        self.button.setText("正在导出…")
        self.notice.set_text("正在导出所选时间与来源的完整日志…", "info")
        self.notice.set_action("取消导出")
        self.notice.action_button.setEnabled(True)
        self.notice.show()
        app = QApplication.instance()
        self.worker = LogExportWorker(self.source_dir, target, self.audit, app,
                                      days=self.range_combo.currentData(), source=self.source_combo.currentData())
        app.aboutToQuit.connect(self.worker.shutdown)
        self.worker.finished.connect(self.export_finished)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()

    def _work_stopping(self):
        owner = getattr(QApplication.instance(), "main_window", None)
        return owner is not None and ui_work_stopping(owner)

    @Slot()
    def cancel_export(self):
        if self.worker is not None:
            self.worker.requestInterruption()
            self.notice.set_text("正在取消导出，已有文件将保留。", "info")
            self.notice.action_button.setEnabled(False)

    @Slot()
    def export_finished(self):
        worker = self.worker
        self.worker = None
        self.button.setEnabled(True)
        self.button.setText("导出日志…")
        self.notice.set_action("")
        if worker.error:
            self.notice.set_text(f"日志未导出：{redact_diagnostic(worker.error)}\n原日志未改动，可调整范围或保存位置后重试。", "error")
        elif worker.warnings:
            self.notice.set_text(f"已保存部分日志至：{worker.target}\n有 {len(worker.warnings)} 项读取失败，文件末尾列出了原因。可刷新后重新导出。", "warning")
        else:
            self.notice.set_text(f"已导出 {worker.count} 个文件、{worker.record_count} 条记录至：\n{worker.target}\n可将此 TXT 文件交给 AI 排查。", "success")
