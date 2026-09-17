"""Settings-only export of existing application logs; no runtime configuration changes."""

import os
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QIODevice, QSaveFile, QStandardPaths, QThread, Qt, Slot
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QPushButton, QVBoxLayout, QWidget

from core.app_version import APP_VERSION
from core.theme import DesignTokens, bind_theme
from ui.primitives import ProductInlineNotice, product_button_style


LOG_TAIL_BYTES = 2 * 1024 * 1024


class LogExportWorker(QThread):
    def __init__(self, source_dir, target, audit, parent):
        super().__init__(parent)
        self.source_dir = Path(source_dir)
        self.target = target
        self.audit = audit
        self.error = ""
        self.count = 0

    @Slot()
    def shutdown(self):
        self.requestInterruption()
        self.wait()

    def run(self):
        output = None
        try:
            files = sorted(
                path for path in self.source_dir.iterdir()
                if path.suffix.lower() == ".log" and path.name != "log_export.log"
                and path.is_file() and not path.is_symlink()
            )
            if not files:
                raise ValueError("暂无可导出的日志。使用应用后可再次尝试。")
            target = Path(self.target).resolve()
            if any(target == path.resolve() or (
                target.exists() and os.path.samefile(target, path)
            ) for path in files):
                raise ValueError("不能覆盖正在使用的日志文件，请选择其他保存位置。")

            self.audit("log_export.log", f"stage=start files={len(files)}")
            output = QSaveFile(self.target)
            if not output.open(QIODevice.WriteOnly):
                raise OSError(output.errorString())

            def write(text):
                data = text.encode("utf-8")
                if output.write(data) != len(data):
                    raise OSError(output.errorString())

            write(
                f"DeepSeek Cowork 日志\n应用版本：{APP_VERSION}\n"
                f"导出时间：{datetime.now().astimezone().isoformat(timespec='seconds')}\n"
                "范围：应用数据目录下的 .log 文件，不包含配置文件或聊天数据库。\n"
                "每个文件最多保留末尾 2 MiB；各文件按读取时的大小截取。\n"
                "日志可能包含路径、错误详情及部分任务内容。\n"
            )
            for path in files:
                if self.isInterruptionRequested():
                    raise InterruptedError("应用退出，日志导出已取消。")
                with path.open("rb") as source:
                    stat = os.fstat(source.fileno())
                    offset = max(0, stat.st_size - LOG_TAIL_BYTES)
                    source.seek(offset)
                    data = source.read(min(stat.st_size, LOG_TAIL_BYTES))
                write(
                    f"\n{'=' * 64}\n日志文件：{path.name}\n"
                    f"原始大小：{stat.st_size} 字节；读取起点：{offset} 字节\n"
                )
                if offset:
                    write("[文件较大，仅导出末尾内容，开头可能是不完整的一行]\n")
                write(data.decode("utf-8-sig", errors="replace") + "\n")
                self.count += 1
            if self.isInterruptionRequested():
                raise InterruptedError("应用退出，日志导出已取消。")
            if not output.commit():
                raise OSError(output.errorString())
            self.audit("log_export.log", f"stage=completed files={self.count}")
        except Exception as exc:
            if output is not None:
                output.cancelWriting()
            self.error = str(exc)
            self.audit("log_export.log", f"stage=failed error_type={type(exc).__name__}")


class LogExportPanel(QWidget):
    def __init__(self, source_dir, audit, parent=None):
        super().__init__(parent)
        self.source_dir = source_dir
        self.audit = audit
        self.worker = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.description = QLabel(
            "下载日志为 TXT 文件，可作为附件交给 AI 排查问题。"
            "包含现有运行日志，每个文件最多保留末尾 2 MiB。"
            "日志可能包含本地路径和部分任务内容。"
        )
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        self.button = QPushButton("下载日志…")
        self.button.setObjectName("SecondaryBtn")
        self.button.clicked.connect(self.export_logs)
        layout.addWidget(self.button, 0, Qt.AlignLeft)
        self.notice = ProductInlineNotice()
        self.notice.label.setTextFormat(Qt.PlainText)
        self.notice.label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.notice.hide()
        layout.addWidget(self.notice)
        self.refresh_theme()
        bind_theme(self, self.refresh_theme)

    def refresh_theme(self, _resolved=None):
        self.description.setStyleSheet(f"color: {DesignTokens.text_secondary};")
        self.button.setStyleSheet(product_button_style("secondary"))

    @Slot()
    def export_logs(self):
        if self.worker is not None:
            return
        directory = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation)
        filename = f"cowork-logs-{datetime.now():%Y%m%d-%H%M%S}.txt"
        dialog = QFileDialog(self, "下载日志", os.path.join(directory, filename))
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
        self.notice.set_text("正在收集日志，请稍候…", "info")
        self.notice.show()
        app = QApplication.instance()
        # The application owns the worker so leaving settings cannot destroy it mid-write.
        self.worker = LogExportWorker(self.source_dir, target, self.audit, app)
        app.aboutToQuit.connect(self.worker.shutdown)
        self.worker.finished.connect(self.export_finished)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()

    @Slot()
    def export_finished(self):
        worker = self.worker
        self.worker = None
        self.button.setEnabled(True)
        self.button.setText("下载日志…")
        if worker.error:
            self.notice.set_text(
                f"日志未导出：{worker.error}\n原日志未改动。可重新选择保存位置后重试。", "error"
            )
        else:
            self.notice.set_text(
                f"已导出 {worker.count} 个日志文件至：\n{worker.target}\n可将此 TXT 文件作为附件交给 AI。",
                "success",
            )
