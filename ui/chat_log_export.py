"""An optional download button; exporting never invokes conversation persistence."""

import os

import qtawesome as qta
from PySide6.QtCore import QIODevice, QSaveFile, QStandardPaths, QThread, Qt, Slot
from PySide6.QtWidgets import QApplication, QFileDialog, QPushButton

from core.chat_log_export import chat_log_filename, iter_chat_log
from core.theme import DesignTokens, bind_theme
from ui.primitives import product_button_style


class ChatLogExportWorker(QThread):
    def __init__(self, snapshot, target, audit, parent):
        super().__init__(parent)
        self.snapshot = snapshot
        self.target = target
        self.audit = audit
        self.error = ""

    @Slot()
    def shutdown(self):
        self.requestInterruption()
        self.wait()

    def run(self):
        output = QSaveFile(self.target)
        session_id = self.snapshot["session_id"]
        try:
            self.audit("chat_log_export.log", f"stage=start session_id={session_id}")
            if not output.open(QIODevice.WriteOnly):
                raise OSError(output.errorString())
            for section in iter_chat_log(self.snapshot):
                if self.isInterruptionRequested():
                    raise InterruptedError("应用退出，导出已取消。")
                data = section.encode("utf-8")
                if output.write(data) != len(data):
                    raise OSError(output.errorString())
            if self.isInterruptionRequested():
                raise InterruptedError("应用退出，导出已取消。")
            if not output.commit():
                raise OSError(output.errorString())
            self.audit("chat_log_export.log", f"stage=completed session_id={session_id}")
        except Exception as exc:
            output.cancelWriting()
            self.error = str(exc)
            self.audit("chat_log_export.log", f"stage=failed session_id={session_id} error_type={type(exc).__name__}")


class ChatLogExportButton(QPushButton):
    def __init__(self, capture_snapshot, notify, audit, parent=None):
        super().__init__("下载聊天记录", parent)
        self.capture_snapshot = capture_snapshot
        self.notify = notify
        self.audit = audit
        self.worker = None
        self.setObjectName("ChatLogExportButton")
        self.setAccessibleName("下载当前对话的聊天记录")
        self.setToolTip("将当前对话导出为 .log 文件；运行中导出已有内容")
        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(self.export_chat)
        self.refresh_theme()
        bind_theme(self, self.refresh_theme)

    def refresh_theme(self, _resolved=None):
        self.setStyleSheet(product_button_style("ghost"))
        self.setIcon(qta.icon("fa5s.download", color=DesignTokens.text_secondary))

    @Slot()
    def export_chat(self):
        if self.worker is not None:
            return
        try:
            # Capture before the native dialog's nested event loop: a session
            # switch or a streaming update must not alter the chosen snapshot.
            snapshot = self.capture_snapshot()
        except Exception as exc:
            self.audit("chat_log_export.log", f"stage=snapshot_failed error_type={type(exc).__name__}")
            self.notify(f"聊天记录未导出：{exc}", "warning")
            return
        directory = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation)
        filename = chat_log_filename(snapshot["title"], snapshot["captured_at"])
        dialog = QFileDialog(self, "下载聊天记录", os.path.join(directory, filename))
        dialog.setAcceptMode(QFileDialog.AcceptSave)
        dialog.setNameFilter("聊天日志 (*.log)")
        dialog.setDefaultSuffix("log")
        if not dialog.exec():
            dialog.deleteLater()
            return
        target = dialog.selectedFiles()[0]
        dialog.deleteLater()
        self.setEnabled(False)
        self.setText("正在导出…")
        app = QApplication.instance()
        self.worker = ChatLogExportWorker(snapshot, target, self.audit, app)
        app.aboutToQuit.connect(self.worker.shutdown)
        self.worker.finished.connect(self.export_finished)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()

    @Slot()
    def export_finished(self):
        worker = self.worker
        self.worker = None
        self.setEnabled(True)
        self.setText("下载聊天记录")
        if worker.error:
            self.notify(
                f"“{worker.snapshot['title']}”的聊天记录未导出：{worker.error}\n"
                "聊天内容已保留，可重新选择保存位置后重试。", "error",
            )
        else:
            self.notify(f"聊天记录已保存：\n{worker.target}", "success")
