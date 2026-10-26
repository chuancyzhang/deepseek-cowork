"""Sidebar update readiness, dismissal, lifecycle and isolated theme previews."""
import os
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QLineEdit, QVBoxLayout, QHBoxLayout

from core.update_coordinator import UpdateCoordinator
from core.theme import ThemeRuntimeManager
from core.theme_service import ThemeRepository
from ui.update_notice import UpdateNoticeController, UpdateReadyNotice


@pytest.fixture
def ui(tmp_path):
    app = QApplication.instance() or QApplication([])
    for name in ("msyh.ttc", "msyhbd.ttc", "consola.ttf"):
        font = Path(os.environ.get("SystemRoot", "C:/Windows"), "Fonts", name)
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
    manager = ThemeRuntimeManager(app, ThemeRepository(str(tmp_path)))
    assert manager.apply_repository_state(reason="update_notice_test"), manager.last_error
    shell = QWidget()
    row = QHBoxLayout(shell)
    sidebar = QWidget(shell)
    sidebar.setFixedWidth(204)
    layout = QVBoxLayout(sidebar)
    layout.setContentsMargins(10, 12, 10, 12)
    layout.addStretch()
    settings = QPushButton("设置", sidebar)
    layout.addWidget(settings)
    row.addWidget(sidebar)
    editor = QLineEdit(shell)
    row.addWidget(editor)
    shell.resize(650, 600)
    shell.show()
    app.processEvents()
    coordinator = UpdateCoordinator(str(tmp_path), True, root=str(tmp_path))
    try:
        yield app, shell, sidebar, settings, editor, coordinator, manager
    finally:
        manager.restore_saved_theme(reason="update_notice_test_cleanup")
        shell.close()
        shell.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        manager.stop()


def ready(coordinator, version="9.0.0"):
    coordinator.prepared = {"release": {"tag_name": version}, "staged_app_dir": "validated-stage",
                            "update_source": "online", "update_available": True}
    coordinator.publish(phase="ready", result=coordinator.prepared)


def test_notice_is_above_settings_without_stealing_focus_and_deduplicates(ui):
    app, _, sidebar, settings, editor, coordinator, _ = ui
    controller = UpdateNoticeController(coordinator, sidebar, settings)
    assert controller.notice.isHidden()
    editor.setFocus()
    app.processEvents()
    ready(coordinator)
    app.processEvents()
    assert app.focusWidget() is editor
    assert controller.notice.isVisible()
    assert sidebar.layout().indexOf(controller.notice) + 1 == sidebar.layout().indexOf(settings)
    assert controller.notice.geometry().bottom() < settings.geometry().top()
    assert controller.notice.width() <= sidebar.width() - 20
    assert controller.notice.install_button.geometry().right() < controller.notice.width()
    controller.notice.close_button.click()
    for _ in range(5):
        ready(coordinator, "v9.0.0")
    assert controller.notice.isHidden()
    assert coordinator.prepared is not None
    assert len(sidebar.findChildren(UpdateReadyNotice)) == 1
    ready(coordinator, "9.0.1")
    assert controller.notice.isVisible()


def test_dismissal_is_process_local_and_install_uses_coordinator(ui):
    _, _, sidebar, settings, _, coordinator, _ = ui
    controller = UpdateNoticeController(coordinator, sidebar, settings)
    ready(coordinator)
    controller.dismiss()
    # A new application presenter has no dismissed version memory.
    restarted = UpdateNoticeController(coordinator, sidebar, settings)
    assert restarted.notice.isVisible()
    with patch.object(coordinator, "_start") as start:
        restarted.notice.install_button.click()
        restarted.notice.install_button.click()
    start.assert_called_once_with("plan", coordinator.prepared)
    assert restarted.notice.isHidden()
    coordinator.install_aborted("保存失败，尚未关闭应用")
    assert restarted.notice.isVisible()


def test_notice_requires_validated_online_package_and_hides_on_invalidation(ui):
    _, _, sidebar, settings, _, coordinator, _ = ui
    controller = UpdateNoticeController(coordinator, sidebar, settings)
    coordinator.publish(phase="downloading", result={"release": {"tag_name": "9.0.0"}})
    assert controller.notice.isHidden()
    ready(coordinator)
    coordinator.prepared = None
    coordinator.publish(phase="error")
    assert controller.notice.isHidden()
    ready(coordinator)
    coordinator.prepared["update_source"] = "local"
    coordinator.publish()
    assert controller.notice.isHidden()
    coordinator.install_enabled = False
    ready(coordinator)
    assert controller.notice.isHidden()


def test_construct_under_active_theme_never_shows_native_children_and_preview_is_reversible(ui):
    app, shell, sidebar, settings, _, coordinator, manager = ui
    class Tracker(QObject):
        def __init__(self):
            super().__init__()
            self.native = []
        def eventFilter(self, obj, event):
            if event.type() == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow() and obj is not shell:
                self.native.append(obj.objectName())
            return False
    tracker = Tracker()
    app.installEventFilter(tracker)
    try:
        ready(coordinator)
        before = manager.repository.load()
        controller = UpdateNoticeController(coordinator, sidebar, settings)
        original = controller.notice.styleSheet()
        assert manager.apply_profile({"id": "update_test", "name": "测试主题", "overrides": {"tokens": {
            "bg_main": "#242424", "text_primary": "#ededed", "primary": "#d5d5d5", "text_inverse": "#242424"}}}, preview=True)
        app.processEvents()
        assert "#242424" in controller.notice.styleSheet()
        assert "#d5d5d5" in controller.notice.install_button.styleSheet()
        assert manager.repository.load() == before
        assert manager.restore_saved_theme(reason="cancel_update_preview")
        assert controller.notice.styleSheet() == original
        assert tracker.native == []
    finally:
        app.removeEventFilter(tracker)
