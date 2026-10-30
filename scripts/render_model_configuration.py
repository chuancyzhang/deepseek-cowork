"""Exercise the real model configuration route with isolated data and no network."""

import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_OPENGL", "software")
os.environ.setdefault("COWORK_EXTERNAL_BACKGROUND_SERVICES", "0")


def run():
    with tempfile.TemporaryDirectory(prefix="cowork-model-ui-") as temporary:
        os.environ["APPDATA"] = temporary
        data = Path(temporary) / "DeepSeekCowork"
        data.mkdir()
        (data / "config.json").write_text("{}", encoding="utf-8")
        from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QWidget
        from core.config_manager import ConfigManager
        from core.theme import ThemeRuntimeManager, default_design_tokens
        from core.theme_service import ThemeRepository
        from main import MainWindow

        app = QApplication.instance() or QApplication([])
        for filename in ("msyh.ttc", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename))
        config = ConfigManager()
        config.set_model_channels([
            {"channel_id": "official", "display_name": "DeepSeek 官方", "provider_type": "openai",
             "api_key": "example-key", "base_url": "https://api.deepseek.com", "models": [
                 {"id": "flash", "model_name": "deepseek-flash", "display_name": "DeepSeek Flash",
                  "api_protocol": "responses", "supports_vision": True,
                  "reasoning_efforts": ["high", "max"], "reasoning_effort": "max"}]},
            {"channel_id": "custom", "display_name": "工作模型", "provider_type": "openai",
             "api_key": "example-key", "base_url": "https://example.test/v1", "models": [
                 {"id": "gpt", "model_name": "gpt-6.1-sol", "display_name": "GPT 工作模型",
                  "api_protocol": "responses", "supports_vision": True,
                  "reasoning_efforts": ["none", "low", "medium", "high", "xhigh", "max"], "reasoning_effort": "low"}]},
        ], "gpt")
        runtime = ThemeRuntimeManager(app, ThemeRepository(config.data_dir))
        app.theme_manager = runtime
        assert runtime.apply_repository_state(reason="model_ui_qa"), runtime.last_error

        class ShowTracker(QObject):
            def __init__(self):
                super().__init__()
                self.stray = []

            def eventFilter(self, obj, event):
                if (event.type() == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow()
                        and obj.parentWidget() is None and not isinstance(obj, MainWindow)):
                    self.stray.append((type(obj).__name__, obj.objectName()))
                return False

        tracker = ShowTracker()
        app.installEventFilter(tracker)
        with patch.object(MainWindow, "start_background_services", lambda self: None):
            window = MainWindow(config_manager=config, theme_manager=runtime)
        window._model_onboarding_shown = True
        # Saving settings may request a skill refresh; it is unrelated to this UI check.
        window.start_background_skill_load = lambda *args, **kwargs: None
        window.resize(1280, 900)
        window.show()
        output = Path(os.environ.get("COWORK_MODEL_SCREENSHOTS", ROOT / "docs/images/model-configuration"))
        output.mkdir(parents=True, exist_ok=True)
        scale = os.environ.get("QT_SCALE_FACTOR", "1")
        suffix = "-150" if scale == "1.5" else ""

        def capture(name):
            QTest.qWait(120)
            assert window.grab().save(str(output / (name + suffix + ".png")))

        def wait_setup(setup):
            deadline = time.monotonic() + 5
            while setup.worker and time.monotonic() < deadline:
                QTest.qWait(10)
            assert setup.worker is None, "connection task must complete"

        origin = window.current_session_id
        window.show_model_selector_popover()
        capture("picker")
        picker = window.model_selector_popover
        QTest.mouseClick(window.input_field, Qt.LeftButton)
        assert not picker.isVisible(), "outside click must close the model picker"
        window.show_model_selector_popover()
        picker = window.model_selector_popover
        picker.search_input.setText("no-such-model")
        capture("picker-empty-search")
        QTest.mouseClick(picker.configure_btn, Qt.LeftButton)
        QTest.qWait(100)
        assert window.current_product_route == window.PAGE_SETTINGS
        page = window.product_pages[window.PAGE_SETTINGS]
        page._automatic_update_check_started = True
        manager = page.model_channel_manager
        assert manager.origin_session_id == origin
        editor = manager.channel_detail_stack.currentWidget()
        form = editor.model_forms[editor._active_form_id]
        form.model_name_input.setText("gpt-6.1-sol")
        form.display_name_input.setText("日常工作模型")
        form.api_protocol_combo.setCurrentIndex(form.api_protocol_combo.findData("responses"))
        form.vision_check.setChecked(True)
        form.thinking_check.setChecked(True)
        for effort in ("none", "low", "medium", "high", "xhigh", "max"):
            form.reasoning_checks[effort].setChecked(True)
        form.reasoning_combo.setCurrentIndex(form.reasoning_combo.findData("low"))
        capture("editor")
        assert not manager.more_btn.isVisible() and not editor.form_navigation.isVisible(), {
            "detail": manager.route_stack.currentWidget() is manager.detail,
            "current_editor": manager.channel_detail_stack.currentWidget() is editor,
            "form_id": editor._active_form_id,
            "more_visible": manager.more_btn.isVisible(), "form_navigation_visible": editor.form_navigation.isVisible()}
        assert not editor.connection_widget.isVisible() and manager.back_btn.isVisible()
        window.resize(760, 760)
        capture("editor-narrow")
        scroll = page._settings_pages[page._page_labels.index("模型与服务")]
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        capture("editor-narrow-bottom")
        test_position = editor.form_test_btn.mapTo(scroll.viewport(), editor.form_test_btn.rect().center())
        assert scroll.viewport().rect().contains(test_position), "scrolling to the bottom must reveal the test action, not blank space"
        assert scroll.horizontalScrollBar().maximum() == 0, "model page must not overflow horizontally"
        assert page.save_settings_btn.isVisible()
        window.resize(1280, 900)
        scroll.verticalScrollBar().setValue(0)
        runtime.switch_theme("default", mode="dark")
        capture("editor-dark")
        runtime.switch_theme("default", mode="light")
        saved_theme = runtime.repository.load()
        candidate = runtime.repository.write_preview(
            name="模型配置预览测试", overrides={"tokens": {"primary": "#39735a", "bg_main": "#f7faf6"}},
            default_tokens=default_design_tokens(), session_id="model-ui-qa")
        runtime.request_preview_update()
        deadline = time.monotonic() + 5
        while runtime.preview is None and time.monotonic() < deadline:
            QTest.qWait(10)
        assert runtime.preview and runtime.preview["preview_id"] == candidate["preview_id"]
        assert runtime.repository.load() == saved_theme
        capture("editor-theme-preview")
        runtime.restore_saved_theme(expected_id=candidate["preview_id"], expected_revision=candidate["revision"])
        assert runtime.repository.load() == saved_theme
        model_id = editor._active_form_id
        form.context_window_input.setText("invalid")
        page.save_settings()
        assert "整数" in form.error_label.text()
        capture("editor-validation")
        form.context_window_input.clear()
        form.advanced_button.setChecked(False)
        page.save_settings()
        assert config.get_model_profile(model_id)["id"] == model_id
        assert manager.saved_model_id == model_id
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        capture("editor-saved")
        other = window.create_new_session(title="另一个对话", make_current=False)
        other_id = other.session_id if hasattr(other, "session_id") else other
        other_before = window._model_id_for_state(window.sessions[other_id])
        QTest.mouseClick(manager.use_model_btn, Qt.LeftButton)
        assert window.current_product_route == window.PAGE_CONVERSATION
        assert window.current_session_id == origin
        assert window._model_id_for_state(window.sessions[origin]) == model_id
        assert window._model_id_for_state(window.sessions[other_id]) == other_before
        assert config.get_selected_model_id() == "gpt"
        capture("returned-to-conversation")
        window.open_settings("模型与服务")
        page = window.product_pages[window.PAGE_SETTINGS]
        page.model_channel_manager.channel_list.setCurrentRow(1)
        page.model_channel_manager.editors[1].show_model_list()
        row = page.model_channel_manager.editors[1].model_list.item(0)
        assert "图片理解" in row.text() and "能力信息不完整" not in row.text()
        scroll = page._settings_pages[page._page_labels.index("模型与服务")]
        scroll.verticalScrollBar().setValue(0)
        capture("model-list")
        manager = page.model_channel_manager
        editor = manager.editors[1]
        assert manager.route_stack.currentWidget() is manager.detail
        assert page.nav_list.isVisible() and page.settings_search.isVisible()
        assert manager.channel_list.isHidden(), "second navigation must be removed"
        manager.show_overview()
        capture("services-overview")
        manager.add_channel()
        capture("service-connect")
        import httpx
        from core.llm.gateway_accounts import GatewayAccount
        setup = manager.setup
        setup.url_input.setText("https://gateway.example.test/login?redirect=%2Fdashboard")
        remote_keys = [
            {"id": 1, "name": "日常工作", "group": "标准组", "status": 1, "unlimited_quota": True},
            {"id": 2, "name": "深度研究", "group": "研究组", "status": 1, "unlimited_quota": True},
            {"id": 3, "name": "旧项目", "group": "标准组", "status": 2}]
        fail_second = [True]
        calls = []
        def gateway_response(request):
            path = request.url.path
            calls.append((request.method, path))
            if path == "/api/status":
                result = {"version": "fixture"}
            elif path == "/api/user/login":
                result = {"access_token": "fixture-management", "user": {"id": 17}}
            elif path == "/api/token/":
                if request.method == "POST":
                    remote_keys.append(dict(json.loads(request.content), id=4, status=1))
                    result = None
                else:
                    result = {"items": remote_keys, "total": len(remote_keys)}
            elif path == "/api/user/self/groups":
                result = {"标准组": {"desc": "标准组"}, "研究组": {"desc": "研究组"}}
            elif path.startswith("/api/token/"):
                if path == "/api/token/2" and fail_second[0]:
                    return httpx.Response(503, request=request)
                result = {"key": "fixture-" + path.rsplit("/", 1)[-1]}
            elif path == "/v1/models":
                result = [{"id": "work-model", "supported_endpoint_types": ["openai-response"], "supports_vision": True,
                           "supported_reasoning_efforts": ["low", "medium", "high"]},
                          {"id": "text-model", "supported_endpoint_types": ["openai"], "supports_vision": False},
                          {"id": "native-video", "supported_endpoint_types": ["video"]}]
            else:
                raise AssertionError(path)
            return httpx.Response(200, json={"success": True, "data": result}, request=request)
        account = GatewayAccount(setup.url_input.text(), "new_api", transport=httpx.MockTransport(gateway_response))
        with patch("ui.gateway_setup.GatewayAccount", return_value=account):
            setup.connect_or_probe()
            wait_setup(setup)
        capture("service-sign-in")
        setup.username.setText("example-user")
        setup.password.setText("fixture-password")
        setup.connect_account()
        wait_setup(setup)
        capture("service-keys")
        window.resize(1440, 1240)
        setup.back_btn.click()
        QTest.qWait(80)
        for field in (setup.mode, setup.url_input, setup.username, setup.password, setup.name_input):
            assert field.parentWidget().height() <= field.parentWidget().sizeHint().height() + 2
        capture("service-sign-in-tall")
        setup.stack.setCurrentWidget(setup.keys_page)
        window.resize(1280, 900)
        setup.prepare_creation()
        wait_setup(setup)
        capture("service-key-create")
        scroll.ensureWidgetVisible(setup.create_submit)
        capture("service-key-create-bottom")
        position = setup.create_submit.mapTo(scroll.viewport(), setup.create_submit.rect().center())
        assert scroll.viewport().rect().contains(position), "creation action must remain reachable by scrolling"
        assert setup.quota.height() >= setup.quota.fontMetrics().height() + 4, "quota input must not clip its text"
        scroll.verticalScrollBar().setValue(0)
        setup.create_key()
        wait_setup(setup)
        for item in setup._key_items():
            item.setCheckState(Qt.Checked if item.data(Qt.UserRole)["id"] in {"1", "2"} else Qt.Unchecked)
        setup.discover()
        wait_setup(setup)
        setup.selection._select_all()
        capture("service-partial-failure")
        runtime.switch_theme("default", mode="dark")
        capture("service-partial-failure-dark")
        runtime.switch_theme("default", mode="light")
        window.resize(760, 760)
        capture("service-partial-failure-narrow")
        assert scroll.horizontalScrollBar().maximum() == 0
        scroll.ensureWidgetVisible(setup.retry_rows)
        capture("service-partial-failure-narrow-bottom")
        scroll.verticalScrollBar().setValue(0)
        window.resize(1280, 900)
        fail_second[0] = False
        setup.retry_failed()
        wait_setup(setup)
        setup.selection._select_all()
        capture("service-model-selection")
        setup.finish_import()
        assert account.client.is_closed
        assert manager.channel_combo.count() == 2
        page.save_settings()
        imported = manager.channel_detail_stack.currentWidget()
        assert "图片理解" in imported.model_list.item(0).text()
        assert "能力信息不完整" not in imported.model_list.item(0).text()
        capture("service-multiple-keys")
        assert not any(path in {"/v1/responses", "/v1/chat/completions"} for _, path in calls)
        manager.add_channel()
        manager.setup.mode.setCurrentIndex(1)
        capture("service-api-key")
        manager.discard_setup()
        manager.open_service("custom")
        runtime.switch_theme("default", mode="dark")
        capture("model-list-dark")
        runtime.switch_theme("default", mode="light")
        window.resize(1440, 1000)
        manager.connection_action.trigger()
        capture("service-connections")
        manager.connection_action.trigger()
        window.resize(760, 760)
        capture("model-list-narrow")
        assert manager.back_btn.isVisible()
        assert page._settings_pages[page._page_labels.index("模型与服务")].horizontalScrollBar().maximum() == 0
        window.resize(1280, 900)
        manager.channel_list.setCurrentRow(0)
        official_editor = manager.editors[0]
        official_editor.open_model_form("flash")
        official_form = official_editor.model_forms["flash"]
        official_form.advanced_button.setChecked(True)
        assert official_form.bootstrap_check.isEnabled() and official_form.bootstrap_check.isChecked()
        window.resize(1440, 1240)
        scroll = page._settings_pages[page._page_labels.index("模型与服务")]
        QTest.qWait(80)
        scroll.verticalScrollBar().setValue(0)
        capture("deepseek-minimal-default")
        window.resize(760, 760)
        QTest.qWait(80)
        scroll.ensureWidgetVisible(official_form.bootstrap_check)
        capture("deepseek-minimal-narrow")
        assert scroll.horizontalScrollBar().maximum() == 0
        window.resize(1280, 900)
        assert not tracker.stray, tracker.stray
        QTest.qWait(80)
        window_dpi = None
        if app.platformName() == "windows":
            import ctypes
            get_dpi = ctypes.windll.user32.GetDpiForWindow
            get_dpi.argtypes = [ctypes.c_void_p]
            get_dpi.restype = ctypes.c_uint
            window_dpi = int(get_dpi(int(window.winId())))
        (output / ("verification" + suffix + ".json")).write_text(json.dumps({
            "logical_window": [window.width(), window.height()], "requested_window": [1280, 900],
            "narrow_window": [760, 760], "qt_scale_factor": scale,
            "device_pixel_ratio": window.devicePixelRatioF(), "unexpected_top_level_windows": tracker.stray,
            "theme_preview_persisted": False, "origin_session_verified": True,
            "model_requests": False,
            "platform_plugin": app.platformName(), "gateway_flow": "probe/login/create/partial-failure/retry/import/save",
            "windows_window_dpi": window_dpi,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        app.removeEventFilter(tracker)
        runtime.stop()
        page._allow_close_without_prompt = True
        if window.tray_icon is not None:
            window.tray_icon.hide()
            window.tray_icon = None
        window.close()
        deadline = time.monotonic() + 10
        while not getattr(window, "_checkpoint_close_ready", False) and time.monotonic() < deadline:
            QTest.qWait(20)
        if not getattr(window, "_checkpoint_close_ready", False):
            from ui.worker_lifecycle import pending_ui_workers
            print("shutdown:", {"pending": getattr(window, "_checkpoint_shutdown_pending", None),
                  "workers": [type(worker).__name__ for worker in pending_ui_workers(window, include_writers=True)],
                  "callbacks": len(window._checkpoint_continuations)}, flush=True)
            for log_path in Path(temporary).rglob("*.log"):
                if "navigation" in log_path.name or "main" in log_path.name:
                    print(log_path.name, log_path.read_text(encoding="utf-8", errors="replace")[-4500:], flush=True)
        assert getattr(window, "_checkpoint_close_ready", False)
        window.file_model.directoryLoaded.disconnect()
        window.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        print("Model configuration route, session isolation, theme and layout checks passed.", flush=True)


if __name__ == "__main__":
    run()
