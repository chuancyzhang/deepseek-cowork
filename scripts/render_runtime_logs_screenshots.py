"""Isolated runtime-log UI fixtures; no real logs, credentials or background app."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / "docs" / "images" / "runtime-logs"


def render(theme, scale, scenario):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_SCALE_FACTOR"] = str(scale)
    with tempfile.TemporaryDirectory(prefix="cowork-logs-preview-") as temporary:
        os.environ["APPDATA"] = os.path.join(temporary, "roaming")
        os.environ["LOCALAPPDATA"] = os.path.join(temporary, "local")
        from datetime import timedelta
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
        from core.runtime_logs import append_log, local_now
        from core.theme import DesignTokens, ThemeRuntimeManager
        from core.theme_service import ThemeRepository
        from ui.log_export import LogExportPanel, LogExportWorker
        from ui.primitives import ProductPageHeader

        app = QApplication([])
        for font in ("msyh.ttc", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["SystemRoot"], "Fonts", font)))
        theme_root = Path(temporary, "theme-preview")
        repo = ThemeRepository(str(theme_root))
        state_path = Path(repo.store_path)
        saved_before = state_path.read_bytes() if state_path.exists() else None
        manager = ThemeRuntimeManager(app, repo)
        colors = {
            "bg_main": "#222327", "bg_app": "#1b1c20", "bg_code": "#222327",
            "bg_secondary": "#2b2d33", "bg_tertiary": "#34363d",
            "bg_disabled": "#34363d", "border_subtle": "#3a3c44", "text_disabled": "#999da8",
            "text_primary": "#f1f1f3", "text_secondary": "#b9bbc2", "text_tertiary": "#999da8",
            "border": "#484b54", "border_strong": "#575a65", "separator": "#3a3c44",
        } if theme == "dark" else {}
        assert manager.apply_profile({"id": "logs-preview", "name": "运行日志预览", "overrides": {"tokens": colors}}, preview=True), manager.last_error
        data = Path(temporary, "log-fixtures")
        now = local_now()
        if scenario != "empty":
            entries = [
                ("startup", "stage=main_window_ready elapsed_ms=420"),
                ("daemon", "stage=run_started session_id=demo-session run_id=demo-run"),
                ("browser_skill_runtime", "stage=service_ready 浏览器服务已连接"),
                ("chat_runtime_debug", "stage=tool_completed task_id=demo-task elapsed_ms=128"),
                ("ui_error", "stage=preview_failed 文件预览暂不可用\nTraceback (most recent call last):\n  File \"D:/Projects/demo/preview.py\", line 42\nOSError: 示例文件暂时被占用"),
                ("daemon", "stage=run_completed session_id=demo-session run_id=demo-run"),
            ]
            for index, (source, message) in enumerate(entries):
                append_log(source, message, data, now=now - timedelta(seconds=len(entries) - index))
        shell = QWidget()
        shell.setObjectName("RuntimeLogsPreview")
        shell.setStyleSheet(f"QWidget#RuntimeLogsPreview {{ background: {DesignTokens.bg_main}; }}")
        layout = QVBoxLayout(shell)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(16)
        layout.addWidget(ProductPageHeader("运行日志", "设置 · 应用维护"))
        panel = LogExportPanel(str(data), lambda *_args: None, shell)
        layout.addWidget(panel, 1)
        shell.resize(1000 if scenario == "overview" else 580 if scenario == "compact" else 760,
                     700 if scenario in {"overview", "compact"} else 620)
        shell.show()
        deadline = time.monotonic() + 10
        app.processEvents()
        while panel._read_worker is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        assert panel._read_worker is None
        if scenario == "partial":
            worker = LogExportWorker(str(data), str(Path(temporary, "example.txt")), lambda *_args: None, app)
            worker.run()
            assert not worker.error
            worker.warnings = ["示例：部分原始输出文件被占用，未能读取。"]
            worker.target = r"C:\Users\示例用户\Downloads\cowork-logs.txt"
            panel.worker = worker
            panel.export_finished()
            panel.notice.show()
            worker.deleteLater()
        app.processEvents()
        for button in (panel.button, panel.refresh_button, panel.more_button, panel.search_button):
            assert button.width() >= button.fontMetrics().horizontalAdvance(button.text()) + 18, button.text()
            assert shell.rect().contains(button.mapTo(shell, button.rect().bottomRight())), button.text()
        assert panel.viewer.height() >= 160
        assert not state_path.exists() if saved_before is None else state_path.read_bytes() == saved_before
        OUTPUT.mkdir(parents=True, exist_ok=True)
        destination = OUTPUT / f"{scenario}-{theme}-{int(scale * 100)}.png"
        assert shell.grab().save(str(destination))
        shell.close()
        shell.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        print(destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=("overview", "compact", "empty", "partial"))
    parser.add_argument("--theme", default="light", choices=("light", "dark"))
    parser.add_argument("--scale", type=float, default=1.25)
    args = parser.parse_args()
    if args.scenario:
        render(args.theme, args.scale, args.scenario)
    else:
        for scenario, theme, scale in (("overview", "light", 1.25), ("compact", "dark", 1.5),
                                       ("empty", "light", 2), ("partial", "light", 1.25)):
            subprocess.run([sys.executable, __file__, "--scenario", scenario, "--theme", theme,
                            "--scale", str(scale)], check=True, timeout=30)
