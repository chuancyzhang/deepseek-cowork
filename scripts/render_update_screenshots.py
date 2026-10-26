"""Render isolated update controls at 125/150/200 percent; no network or app install."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / "docs" / "guides" / "update-ui"


def workspace(theme, scale):
    """Show the real sidebar integration, with isolated data and no background services."""
    os.environ.update(QT_QPA_PLATFORM="offscreen", QT_SCALE_FACTOR=str(scale),
                      COWORK_EXTERNAL_BACKGROUND_SERVICES="0", QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu")
    with tempfile.TemporaryDirectory(prefix="cowork-update-workspace-") as directory:
        os.environ["APPDATA"] = str(Path(directory, "roaming"))
        os.environ["LOCALAPPDATA"] = str(Path(directory, "local"))
        import core.env_utils
        core.env_utils.get_app_data_dir = lambda: directory
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtWidgets import QApplication, QMainWindow
        from core.config_manager import ConfigManager
        from core.theme import ThemeRuntimeManager
        from core.theme_service import ThemeRepository
        from core.theme_presets import builtin_theme
        from main import MainWindow
        app = QApplication([])
        for font in ("msyh.ttc", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["SystemRoot"], "Fonts", font)))
        repository = ThemeRepository(directory)
        manager = ThemeRuntimeManager(app, repository)
        app.theme_manager = manager
        with patch.object(MainWindow, "showEvent", lambda window, event: QMainWindow.showEvent(window, event)), \
             patch("core.config_manager.get_app_data_dir", return_value=directory), \
             patch("core.config_manager.get_base_dir", return_value=directory):
            window = MainWindow(config_manager=ConfigManager(), theme_manager=manager)
            window.resize(760 if scale == 2 else 1100, 760)
            window.show()
            state_path = Path(repository.store_path)
            saved = state_path.read_bytes() if state_path.exists() else None
            assert manager.apply_profile(builtin_theme("default", dark=theme == "dark"), preview=True), manager.last_error
            coordinator = window.update_coordinator
            coordinator.install_enabled = True  # Isolated UI fixture; never start an installer.
            coordinator.prepared = {"release": {"tag_name": "9.0.0"}, "staged_app_dir": directory,
                                    "update_available": True, "update_source": "online"}
            coordinator.publish(phase="ready", result=coordinator.prepared)
            for _ in range(10):
                app.processEvents(); time.sleep(.03)
            window.sync_context_drawer_layout(); app.processEvents()
            notice = window.update_notice.notice
            settings = window.product_nav_buttons[window.PAGE_SETTINGS]
            assert notice.isVisible() and not notice.isWindow()
            assert notice.geometry().bottom() < settings.geometry().top()
            assert notice.install_button.geometry().right() < notice.width()
            assert window.grab().save(str(OUTPUT / f"workspace-{theme}-{int(scale*100)}.png"))
            notice.close_button.click()
            coordinator.publish()
            assert notice.isHidden() and coordinator.prepared
            assert (state_path.read_bytes() if state_path.exists() else None) == saved
            manager.restore_saved_theme(reason="update_workspace_capture_finished")
            manager.stop()
            window.close()
            deadline = time.monotonic() + 8
            while not getattr(window, "_checkpoint_close_ready", False) and time.monotonic() < deadline:
                app.processEvents(); time.sleep(.01)
            assert window._checkpoint_close_ready
            window.file_model.directoryLoaded.disconnect()
            window.deleteLater(); app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def panel(theme, scale, failure, waiting=False, enterprise=False, notice=False):
    os.environ["QT_SCALE_FACTOR"] = str(scale)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QLabel, QPushButton, QLineEdit
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFontDatabase
    from core.theme import ThemeRuntimeManager, DesignTokens
    from core.theme_service import ThemeRepository
    from core.update_coordinator import UpdateCoordinator
    from ui.update_panel import UpdatePanel
    from ui.update_notice import UpdateNoticeController
    from ui.primitives import product_button_style
    app = QApplication([])
    for filename in ("msyh.ttc", "consola.ttf"):
        QFontDatabase.addApplicationFont(str(Path(os.environ["SystemRoot"], "Fonts", filename)))
    with tempfile.TemporaryDirectory(prefix="cowork-update-ui-") as directory:
        manager = ThemeRuntimeManager(app, repository=ThemeRepository(directory))
        colors = {"bg_main": "#222327", "bg_app": "#1b1c20", "bg_secondary": "#2b2d33", "bg_tertiary": "#34363d",
                  "text_primary": "#f1f1f3", "text_secondary": "#b9bbc2", "text_tertiary": "#999da8", "border": "#484b54",
                  "border_strong": "#575a65", "separator": "#3a3c44", "primary": "#d5d5d5", "text_inverse": "#242424",
                  "bg_sidebar": "#202020"} if theme == "dark" else {}
        assert manager.apply_profile({"id": "update_qa", "name": "更新界面验证", "overrides": {"tokens": colors}}, preview=True), manager.last_error
        coordinator = UpdateCoordinator(directory, True, root=directory)
        result = {"release": {"tag_name": "v9.0.0", "body": "## 更新体验\n\n- 启动后提前准备安装包\n- 更新时保留已完成内容和输入草稿\n- 完成后自动重新打开应用\n\n![不加载外部图片](https://example.invalid/image.png)"}, "update_available": True,
                  "update_source": "online", "staged_app_dir": directory, "source_name": "github"}
        if enterprise:
            coordinator.connection = {"platform_name": "示例企业"}
            result["connection"] = coordinator.connection
        if not failure:
            coordinator.prepared = result
        coordinator.publish(phase="error" if failure else "ready", message="暂时无法下载更新，可重试或选择本地安装包" if failure else "更新已准备好", result=result, error="更新服务连接超时，已尝试 5 次。" if failure else "")
        if waiting:
            coordinator.publish(phase="installing", can_cancel_install=True, percent=-1,
                                message="正在等待安装程序启动；若安全软件弹出确认，请允许本次安装。应用和任务暂时保持运行。")
        if notice:
            shell = QWidget()
            shell.setStyleSheet(f"background: {DesignTokens.bg_main}; color: {DesignTokens.text_primary};")
            row = QHBoxLayout(shell); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(0)
            sidebar = QWidget(shell); sidebar.setFixedWidth(204 if scale == 2 else 240)
            sidebar.setObjectName("UpdatePreviewSidebar")
            sidebar.setStyleSheet(f"QWidget#UpdatePreviewSidebar {{background: {DesignTokens.bg_sidebar};}}")
            side = QVBoxLayout(sidebar); side.setContentsMargins(10, 28, 10, 12); side.setSpacing(8)
            for text in ("新建聊天", "项目与对话", "发布准备", "本周工作"):
                label = QLabel(text, sidebar); label.setContentsMargins(8, 8, 8, 8); side.addWidget(label)
            side.addStretch()
            for text in ("团队空间", "能力", "常用", "资料库", "设置"):
                button = QPushButton(text, sidebar); button.setStyleSheet(product_button_style("ghost")); side.addWidget(button)
            controller = UpdateNoticeController(coordinator, sidebar, button)
            row.addWidget(sidebar)
            content = QWidget(shell); body = QVBoxLayout(content); body.setContentsMargins(24, 24, 24, 24)
            title = QLabel("继续当前工作", content); title.setStyleSheet("font-size:20px; font-weight:600;"); body.addWidget(title)
            message = QLabel("安装包已在后台准备好。\n方便时点击侧栏中的“重启并更新”即可。", content)
            message.setWordWrap(True); body.addWidget(message); body.addStretch()
            editor = QLineEdit(content); editor.setPlaceholderText("输入内容继续工作…"); body.addWidget(editor)
            row.addWidget(content, 1)
            shell.resize(640 if scale == 2 else 920, 620)
            shell.show(); editor.setFocus(); app.processEvents()
            assert controller.notice.isVisible()
            assert controller.notice.geometry().bottom() < button.geometry().top()
            assert controller.notice.install_button.geometry().right() < controller.notice.width()
            assert shell.grab().save(str(OUTPUT / f"notice-{theme}-{int(scale*100)}.png"))
            shell.close(); shell.deleteLater(); app.processEvents(); manager.stop()
            return
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
        colors = {"bg": "#242424", "text": "#ededed", "primary": "#d5d5d5"} if theme == "dark" else {"bg": "#ffffff", "text": "#303030", "primary": "#404040"}
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
    parser.add_argument("--notice", action="store_true")
    parser.add_argument("--workspace", action="store_true")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if args.workspace:
        workspace(args.theme, args.scale)
    elif args.panel:
        panel(args.theme, args.scale, args.failure, args.waiting, args.enterprise, args.notice)
    else:
        for theme, scale, failure in [("light", 1.25, False), ("dark", 1.5, False), ("light", 2, False), ("dark", 2, True)]:
            command = [sys.executable, __file__, "--panel", "--theme", theme, "--scale", str(scale)]
            if failure:
                command.append("--failure")
            subprocess.run(command, check=True, timeout=30)
        for theme, scale in [("light", 1.25), ("dark", 1.5), ("light", 2)]:
            installer(theme, scale)
            subprocess.run([sys.executable, __file__, "--panel", "--notice", "--theme", theme, "--scale", str(scale)], check=True, timeout=30)
        print(f"Rendered 10 update UI captures to {OUTPUT}")
