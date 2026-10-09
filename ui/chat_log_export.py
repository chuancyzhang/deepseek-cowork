"""Session-bound exports, independent of selected page or button lifetime."""
import json
import os

from PySide6.QtCore import QObject, QIODevice, QSaveFile, QStandardPaths, QThread, Signal, Slot
from PySide6.QtWidgets import QApplication, QFileDialog
from core.chat_log_export import chat_log_filename, iter_chat_log


class ChatLogExportWorker(QThread):
    def __init__(self, snapshot, target, audit, parent, kind="chat"):
        super().__init__(parent)
        self.snapshot, self.target, self.audit, self.kind = snapshot, target, audit, kind
        self.error = ""

    @Slot()
    def shutdown(self):
        self.requestInterruption()
        self.wait()

    def run(self):
        output = QSaveFile(self.target)
        session_id = self.snapshot["session_id"]
        try:
            self.audit("chat_log_export.log", f"stage=start session_id={session_id} kind={self.kind}")
            if not output.open(QIODevice.WriteOnly):
                raise OSError(output.errorString())
            sections = (iter_chat_log(self.snapshot) if self.kind == "chat" else
                        (json.dumps(self.snapshot["request"], ensure_ascii=False, indent=2) + "\n",))
            for section in sections:
                if self.isInterruptionRequested():
                    raise InterruptedError("应用退出，导出已取消。")
                data = section.encode("utf-8")
                if output.write(data) != len(data):
                    raise OSError(output.errorString())
            if self.isInterruptionRequested():
                raise InterruptedError("应用退出，导出已取消。")
            if not output.commit():
                raise OSError(output.errorString())
            self.audit("chat_log_export.log", f"stage=completed session_id={session_id} kind={self.kind}")
        except Exception as exc:
            output.cancelWriting()
            self.error = str(exc)
            self.audit("chat_log_export.log", f"stage=failed session_id={session_id} kind={self.kind} error_type={type(exc).__name__}")


class ConversationExportController(QObject):
    saved = Signal(str, str, str, str)

    def __init__(self, capture_snapshot, notify, audit, parent):
        super().__init__(parent)
        self.capture_snapshot, self.notify, self.audit = capture_snapshot, notify, audit
        self.workers = {}
        self.pending = set()

    def busy(self, session_id, kind):
        return (session_id, kind) in self.pending

    def export(self, session_id, kind="chat"):
        key = (session_id, kind)
        if key in self.pending:
            return
        self.pending.add(key)
        label = "聊天记录" if kind == "chat" else "最近一次请求"
        try:
            # Freeze before entering the native dialog's nested event loop.
            snapshot = self.capture_snapshot(session_id, kind)
            filename = chat_log_filename(snapshot["title"], snapshot["captured_at"])
            suffix = "log" if kind == "chat" else "json"
            if kind != "chat":
                filename = filename.replace("聊天记录-", "AI请求-", 1).removesuffix(".log") + ".json"
            directory = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation)
            dialog = QFileDialog(self.parent(), "下载" + label, os.path.join(directory, filename))
            dialog.setAcceptMode(QFileDialog.AcceptSave)
            dialog.setNameFilter(f"{label} (*.{suffix})")
            dialog.setDefaultSuffix(suffix)
            accepted = dialog.exec()
            targets = dialog.selectedFiles()
            dialog.deleteLater()
            if not accepted:
                self.pending.discard(key)
                self.audit("chat_log_export.log", f"stage=cancelled session_id={session_id} kind={kind}")
                return
            app = QApplication.instance()
            worker = ChatLogExportWorker(snapshot, targets[0], self.audit, app, kind)
            self.workers[key] = worker
            app.aboutToQuit.connect(worker.shutdown)
            worker.finished.connect(self.export_finished)
            self.notify(f"正在导出“{snapshot['title']}”的{label}…", "info")
            worker.start()
        except Exception as exc:
            self.pending.discard(key)
            self.audit("chat_log_export.log", f"stage=snapshot_failed session_id={session_id} kind={kind} error_type={type(exc).__name__}")
            self.notify(f"{label}未导出：{exc}\n原有内容已保留。", "error")

    @Slot()
    def export_finished(self):
        worker = self.sender()
        key = (worker.snapshot["session_id"], worker.kind)
        self.workers.pop(key, None)
        self.pending.discard(key)
        if worker.error:
            self.notify(f"“{worker.snapshot['title']}”未能导出：{worker.error}\n原有内容已保留，可重新选择保存位置后重试。", "error")
        else:
            self.saved.emit(key[0], key[1], worker.snapshot["title"], worker.target)
        worker.deleteLater()
