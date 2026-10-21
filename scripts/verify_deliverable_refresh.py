"""Isolated real-window/WebEngine refresh checks and user-guide screenshots."""
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run():
    with tempfile.TemporaryDirectory(prefix="cowork-deliverable-qa-") as temporary:
        base = Path(temporary)
        os.environ.update(APPDATA=str(base / "roaming"), LOCALAPPDATA=str(base / "local"),
                          QT_QPA_PLATFORM="offscreen", COWORK_EXTERNAL_BACKGROUND_SERVICES="0")
        # Windows offscreen capture has no reliable D3D surface. This is a test
        # configuration only; the application's rendering configuration is unchanged.
        os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
        config = base / "roaming" / "DeepSeekCowork" / "config.json"
        config.parent.mkdir(parents=True)
        config.write_text("{}", encoding="utf-8")
        from PySide6.QtCore import QEvent, QThread, QTimer
        from PySide6.QtGui import QFont, QFontDatabase
        from PySide6.QtWidgets import QApplication, QMainWindow
        import main
        from ui.worker_lifecycle import pending_ui_workers

        app = QApplication([])
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "consola.ttf"):
            font = Path(os.environ.get("WINDIR", "C:/Windows"), "Fonts", name)
            if font.exists():
                QFontDatabase.addApplicationFont(str(font))
        app.setFont(QFont("Microsoft YaHei UI", 10))
        main.initialize_desktop_theme(app)
        from core.theme_service import ThemeRepository
        manager = main.ThemeRuntimeManager(app, ThemeRepository(str(base / "themes")))
        app.theme_manager = manager
        assert manager.apply_repository_state(reason="refresh_qa")
        errors, quit_events, results = [], [], []
        app.aboutToQuit.connect(lambda: quit_events.append(True))
        output = ROOT / "docs/guides/user-guide"
        scale = os.environ.get("QT_SCALE_FACTOR", "1")
        workspace = base / "workspace"
        workspace.mkdir()
        html = workspace / "周报.html"
        css = workspace / "report.css"
        note = workspace / "会议纪要.txt"
        css.write_text("body{color:rgb(30, 70, 90);font:18px sans-serif;padding:32px}", encoding="utf-8")
        html.write_text('<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="report.css">'
                        '<h1 id="title">项目周报</h1><p>本周成果已保存，可继续查看和编辑。</p>'
                        '<div style="height:1800px">进展记录</div>', encoding="utf-8")
        note.write_text("会议纪要第一版\n", encoding="utf-8")

        def until(predicate, description, timeout=10):
            deadline = time.monotonic() + timeout
            while not predicate() and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.005)
            assert predicate(), description
            assert not errors, errors

        with patch.object(main.MainWindow, "showEvent", lambda w, e: QMainWindow.showEvent(w, e)), \
             patch.object(main.MainWindow, "_show_model_onboarding_if_needed", lambda w: None), \
             patch("sys.excepthook", lambda kind, value, trace: errors.append(str(value))):
            window = main.MainWindow()
            window.tray_icon = None
            window.stop_daemon_process = lambda: None
            window.stop_gateway_process = lambda: None
            window.resize(1280, 800)
            window.show()

            def settled():
                return (window.deliverable_scan_worker is None
                        and window.deliverable_scan_pending is None
                        and not window.deliverable_refresh_timer.isActive())

            def js(expression):
                values = []
                window.deliverable_web_view.page().runJavaScript(expression, values.append)
                until(lambda: values, "WebEngine JavaScript callback")
                return values[0]

            def capture(name):
                from PySide6.QtTest import QTest
                QTest.qWait(200)
                label = window.file_browser_empty_state.description_label
                assert label.height() >= label.heightForWidth(label.width()), (
                    "scan failure reason was clipped", label.size(), label.heightForWidth(label.width()),
                    window.file_browser_empty_state.size(), window.file_browser_empty_state.sizeHint(),
                    window.file_browser_empty_state.heightForWidth(window.file_browser_empty_state.width()))
                action = window.file_browser_empty_state.action_button
                assert window.file_navigator_panel.rect().contains(
                    action.mapTo(window.file_navigator_panel, action.rect().bottomRight()))
                assert window.grab().save(str(output / f"10-deliverable-refresh-{name}-{scale}x.png"))

            def exercise():
                try:
                    window.load_workspace(str(workspace), refresh_sidebar=False,
                        remember_workspace=False, persist_default=False, bind_session=True)
                    for path in (html, note):
                        window.chat_storage.register_deliverable(str(workspace), str(path),
                            conversation_id=window.current_session_id, source="generated")
                    window.show_context_drawer(window.RIGHT_TAB_FILES)
                    window.context_drawer_expanded = True
                    window.context_drawer_user_width = 900
                    window.set_file_navigator_scope(window.FILE_SCOPE_DELIVERABLES, refresh=True,
                                                    user_initiated=True)
                    window.file_navigator_pinned = True
                    window.set_file_navigator_visible(True, reason="verification")
                    window.sync_context_drawer_layout()
                    until(lambda: settled() and len(window.deliverable_items) == 2, "registered list")
                    scanner = window.deliverable_scanner
                    window.select_deliverable(str(html), render_html=True)
                    until(lambda: window.deliverable_web_view is not None, "HTML preview created")
                    until(lambda: js("document.getElementById('title')?.textContent") == "项目周报", "HTML rendered")
                    for _ in range(40):
                        window.refresh_deliverables(render_current=True, immediate=True, reason="manual")
                        until(settled, "reused scanner completed")
                    assert window.deliverable_scanner is scanner
                    assert len(window.findChildren(main.DeliverableScanWorker)) == 1
                    html.write_text(html.read_text(encoding="utf-8").replace("项目周报", "项目周报 · 已更新"), encoding="utf-8")
                    until(lambda: js("document.getElementById('title')?.textContent") == "项目周报 · 已更新", "external HTML refresh")
                    css.write_text("body{color:rgb(10, 100, 50);font:18px sans-serif;padding:32px}", encoding="utf-8")
                    until(lambda: js("getComputedStyle(document.body).color") == "rgb(10, 100, 50)", "linked CSS refresh")
                    until(settled, "filesystem scans drained")
                    results.append("HTML/CSS refresh and 40 reusable scans")

                    # The actual text editor must keep its draft through external writes.
                    window.select_deliverable(str(note), render_html=False)
                    window.begin_deliverable_edit()
                    until(lambda: window.deliverable_edit_state == "ready", "text editor ready")
                    window.deliverable_text_editor.setPlainText("尚未保存的会议补充\n")
                    assert window.deliverable_edit_dirty
                    note.write_text("其他程序更新的会议纪要\n", encoding="utf-8")
                    until(lambda: window.deliverable_edit_state == "conflict", "external draft conflict")
                    until(settled, "list refresh alongside dirty editor")
                    assert window.deliverable_text_editor.toPlainText() == "尚未保存的会议补充\n"
                    assert note.read_text(encoding="utf-8") == "其他程序更新的会议纪要\n"
                    results.append("dirty draft and disk version both preserved")
                    window._release_deliverable_edit_session(show_preview=False)
                    window.select_deliverable(str(html), render_html=True)
                    window.set_file_navigator_visible(True, reason="verification")
                    until(settled, "preview restored")
                    until(lambda: js("document.getElementById('title')?.textContent") == "项目周报 · 已更新", "restored HTML rendered")
                    js("scrollTo(0, 0)")

                    kept = list(window.deliverable_items)
                    with patch.object(window.chat_storage, "list_deliverables", side_effect=OSError("工作区暂时无法读取")):
                        window.refresh_deliverables(immediate=True, reason="manual")
                        until(settled, "failed scan completed")
                    assert window.deliverable_items == kept
                    assert window.file_browser_empty_state.isVisible()
                    assert window.deliverables_list.isVisible()
                    capture("error-light")
                    saved = manager.repository.load()
                    assert manager.apply_profile({"id": "refresh-qa-dark", "name": "深色预览", "schema_version": 2,
                        "overrides": {"tokens": {"bg_main": "#202124", "bg_app": "#202124", "bg_hover": "#35373d",
                            "bg_secondary": "#2b2d33", "bg_tertiary": "#34363d", "bg_sidebar": "#202124",
                            "bg_card": "#202124", "bg_chat": "#202124", "bg_panel": "#202124",
                            "bg_panel_strong": "#202124", "bg_sidebar_selected": "#35373d", "bg_sidebar_hover": "#35373d",
                            "composer_bg": "#202124", "composer_text": "#eeeeee", "composer_border": "#414349",
                            "right_sidebar_bg": "#202124", "right_sidebar_header_bg": "#202124", "right_sidebar_text": "#eeeeee",
                            "right_sidebar_text_muted": "#b5b6ba", "right_sidebar_border": "#414349",
                            "preview_shell_bg": "#202124", "preview_shell_toolbar_bg": "#202124", "preview_shell_text": "#eeeeee",
                            "preview_shell_text_muted": "#b5b6ba", "preview_shell_border": "#414349",
                            "overlay_text": "#eeeeee", "primary_soft": "#35395c", "primary_focus": "#777fd5",
                            "text_primary": "#eeeeee", "text_secondary": "#b5b6ba", "text_tertiary": "#92959b",
                            "border_subtle": "#414349", "overlay_bg": "#202124", "overlay_border": "#414349"}},
                        "assets": {}, "surfaces": {}, "components": {}, "content": {},
                        "workspace_scene": {"attachment": "fixed", "layers": []}}, preview=True, reason="refresh_qa")
                    window.resize(960, 680)
                    capture("error-dark-small")
                    assert manager.restore_saved_theme(reason="refresh_qa_cancel")
                    assert manager.repository.load() == saved
                    window.refresh_deliverables(immediate=True, reason="retry")
                    until(settled, "explicit retry")
                    assert not scanner.error
                    results.append("failure retention, retry, and theme cancellation")
                    assert not errors, errors
                    window.quit_app()
                except Exception:
                    errors.append(traceback.format_exc())
                    print(errors[-1], flush=True)
                    app.exit(1)

            watchdog = QTimer()
            watchdog.setSingleShot(True)
            watchdog.timeout.connect(lambda: app.exit(91))
            watchdog.start(60000)
            QTimer.singleShot(0, exercise)
            code = app.exec()
            watchdog.stop()
            if code or errors:
                print(f"Qt exit={code}, errors={errors}", flush=True)
            try:
                assert code == 0 and not errors, (code, errors)
                assert quit_events == [True]
                assert not window.isVisible()
                assert not pending_ui_workers(window, include_writers=True)
                results.append("real QApplication exit and worker shutdown")
            finally:
                window._checkpoint_shutdown_pending = True
                window.deliverable_refresh_timer.stop()
                for worker in window.findChildren(QThread):
                    if hasattr(worker, "request_stop"):
                        worker.request_stop()
                    assert worker.wait(3000), type(worker).__name__
                window._dispose_application_event_filters()
                window.deleteLater()
                app.sendPostedEvents(None, QEvent.DeferredDelete)
                manager.stop()
            print("Verified: " + "; ".join(results))


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
