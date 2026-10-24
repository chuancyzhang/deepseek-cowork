"""Render isolated update controls at 125/150/200 percent; no network or app install."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / "docs" / "guides" / "update-ui"


def panel(theme, scale, failure, waiting=False, enterprise=False):
    os.environ["QT_SCALE_FACTOR"] = str(scale)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QScrollArea, QLabel
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFontDatabase
    from core.theme import ThemeRuntimeManager, DesignTokens
    from core.theme_service import ThemeRepository
    from core.update_coordinator import UpdateCoordinator
    from ui.update_panel import UpdatePanel
    app = QApplication([])
    for filename in ("msyh.ttc", "consola.ttf"):
        QFontDatabase.addApplicationFont(str(Path(os.environ["SystemRoot"], "Fonts", filename)))
    with tempfile.TemporaryDirectory(prefix="cowork-update-ui-") as directory:
        manager = ThemeRuntimeManager(app, repository=ThemeRepository(directory))
        colors = {"bg_main": "#222327", "bg_app": "#1b1c20", "bg_secondary": "#2b2d33", "bg_tertiary": "#34363d",
                  "text_primary": "#f1f1f3", "text_secondary": "#b9bbc2", "text_tertiary": "#999da8", "border": "#484b54",
                  "border_strong": "#575a65", "separator": "#3a3c44", "primary": "#8e99eb"} if theme == "dark" else {}
        assert manager.apply_profile({"id": "update_qa", "name": "更新界面验证", "overrides": {"tokens": colors}}, preview=True), manager.last_error
        coordinator = UpdateCoordinator(directory, True, root=directory)
        result = {"release": {"tag_name": "v9.0.0", "body": "## 更新体验\n\n- 启动后提前准备安装包\n- 更新时保留已完成内容和输入草稿\n- 完成后自动重新打开应用\n\n![不加载外部图片](https://example.invalid/image.png)"}, "update_available": True}
        if enterprise:
            coordinator.connection = {"platform_name": "示例企业"}
            result["connection"] = coordinator.connection
        if not failure:
            coordinator.prepared = result
        coordinator.publish(phase="error" if failure else "ready", message="暂时无法下载更新，可重试或选择本地安装包" if failure else "更新已准备好", result=result, error="更新服务连接超时，已尝试 5 次。" if failure else "")
        if waiting:
            coordinator.publish(phase="installing", can_cancel_install=True, percent=-1,
                                message="正在等待安装程序启动；若安全软件弹出确认，请允许本次安装。应用和任务暂时保持运行。")
        shell = QWidget()
        shell.setStyleSheet(f"background: {DesignTokens.bg_main};")
        layout = QVBoxLayout(shell); layout.setContentsMargins(24, 24, 24, 24); layout.setSpacing(24)
        title = QLabel("更新与关于"); title.setStyleSheet(f"font-size: 20px; font-weight: 600; color: {DesignTokens.text_primary};"); layout.addWidget(title)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget(); inner = QVBoxLayout(content); inner.setContentsMargins(0, 0, 0, 0)
        control = UpdatePanel(coordinator); inner.addWidget(control); inner.addStretch()
        scroll.setWidget(content); layout.addWidget(scroll)
        shell.resize(480 if scale == 2 else 760, 650 if scale == 2 else 540)
        shell.show(); app.processEvents()
        for button in (control.primary, control.local):
            assert button.width() >= button.fontMetrics().horizontalAdvance(button.text()) + 20
        assert control.notes.text().find("https://") == -1
        if waiting:
            assert control.cancel_button.isVisible()
            assert control.cancel_button.isEnabled()
            assert not control.primary.isEnabled()
        name = f"{'enterprise-' if enterprise else ''}{'waiting' if waiting else 'error' if failure else 'ready'}-{theme}-{int(scale*100)}.png"
        assert shell.grab().save(str(OUTPUT / name))
        shell.close(); shell.deleteLater(); app.processEvents()


def installer(theme, scale):
    from core.update_installer import write_installer
    from core.process_utils import subprocess_kwargs_no_window
    with tempfile.TemporaryDirectory(prefix="cowork-installer-ui-") as directory:
        colors = {"bg": "#222327", "text": "#f1f1f3", "primary": "#8e99eb"} if theme == "dark" else {}
        script = write_installer(directory, directory, str(Path(directory, "plan.json")), directory, [os.getpid()], "fixture.exe", colors, "9.0.0")
        command = [str(Path(os.environ["SystemRoot"], "System32", "WindowsPowerShell", "v1.0", "powershell.exe")),
                   "-NoProfile", "-ExecutionPolicy", "Bypass", "-STA", "-File", script,
                   "-PreviewImage", str(OUTPUT / f"installer-{theme}-{int(scale*100)}.png"), "-PreviewScale", str(scale)]
        result = subprocess.run(command, capture_output=True, timeout=25, **subprocess_kwargs_no_window())
        assert result.returncode == 0, result.stderr.decode(errors="replace")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--panel", action="store_true")
    parser.add_argument("--theme", default="light")
    parser.add_argument("--scale", type=float, default=1.25)
    parser.add_argument("--failure", action="store_true")
    parser.add_argument("--waiting", action="store_true")
    parser.add_argument("--enterprise", action="store_true")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if args.panel:
        panel(args.theme, args.scale, args.failure, args.waiting, args.enterprise)
    else:
        for theme, scale, failure in [("light", 1.25, False), ("dark", 1.5, False), ("light", 2, False), ("dark", 2, True)]:
            command = [sys.executable, __file__, "--panel", "--theme", theme, "--scale", str(scale)]
            if failure:
                command.append("--failure")
            subprocess.run(command, check=True, timeout=30)
        for theme, scale in [("light", 1.25), ("dark", 1.5), ("light", 2)]:
            installer(theme, scale)
        print(f"Rendered 7 update UI captures to {OUTPUT}")
