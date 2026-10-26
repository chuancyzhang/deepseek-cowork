"""Profile local navigation with isolated data and no remote requests."""
import argparse
import cProfile
import io
import json
import os
from pathlib import Path
import pstats
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "test")]


def run(output, *, native=False, scale=1, dark=False, compact=False, idle_seconds=0, screenshots=False):
    os.environ["QT_QPA_PLATFORM"] = "windows" if native else "offscreen"
    os.environ["QT_SCALE_FACTOR"] = str(scale)
    os.environ["COWORK_EXTERNAL_BACKGROUND_SERVICES"] = "0"
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cowork-ui-performance-") as temporary:
        os.environ.update(APPDATA=temporary, LOCALAPPDATA=temporary)
        import core.env_utils
        core.env_utils.get_app_data_dir = lambda: temporary
        from PySide6.QtGui import QFontDatabase
        from core.config_manager import ConfigManager
        from core.theme import ThemeRuntimeManager
        from core.theme_presets import builtin_theme
        from core.theme_service import ThemeRepository
        from main import MainWindow, SafeApplication, SkillsCenterDialog, initialize_desktop_theme
        from ui_window_cleanup import close_test_window

        class ProfileApplication(SafeApplication):
            def __init__(self):
                self.profile_thread = threading.get_ident()
                self.event_depth = 0
                self.slow_events = []
                self.profile_phase = "startup"
                super().__init__([])

            def notify(self, receiver, event):
                if threading.get_ident() != self.profile_thread:
                    return super().notify(receiver, event)
                outer = self.event_depth == 0
                if outer:
                    started = time.perf_counter()
                    identity = {"receiver": type(receiver).__name__, "event": event.type().name,
                                "phase": self.profile_phase}
                self.event_depth += 1
                try:
                    return super().notify(receiver, event)
                finally:
                    self.event_depth -= 1
                    if outer:
                        elapsed = (time.perf_counter() - started) * 1000
                        if elapsed >= 100:
                            self.slow_events.append({**identity, "elapsed_ms": round(elapsed, 2)})

        app = ProfileApplication()
        for font in ("msyh.ttc", "msyhbd.ttc", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["SystemRoot"], "Fonts", font)))
        initialize_desktop_theme(app)
        manager = ThemeRuntimeManager(app, ThemeRepository(temporary))
        app.theme_manager = manager
        assert manager.apply_repository_state(reason="navigation_profile"), manager.last_error
        with patch("core.config_manager.get_app_data_dir", return_value=temporary), \
                patch("core.config_manager.get_base_dir", return_value=temporary), \
                patch.object(MainWindow, "start_background_services", lambda self: None), \
                patch.object(SkillsCenterDialog, "_load_hub", lambda self, **kwargs: None), \
                patch.object(SkillsCenterDialog, "_probe_browser_component_status", lambda self: None):
            window = MainWindow(ConfigManager(), theme_manager=manager)
            window._startup_hydration_scheduled = True
            window._background_services_scheduled = True
            window._model_onboarding_shown = True
            window.skill_manager = window.skill_catalog_service.preload().manager
            window.skill_manager_ready = True
            saved_theme = manager.repository.load()
            assert manager.apply_profile(builtin_theme("default", dark=dark), preview=True)
            window.resize(1000 if compact else 1440, 760 if compact else 900)
            window.setWindowTitle("Cowork 性能验证（隔离数据）")
            window.show()
            for _ in range(10):
                app.processEvents()
            measurements = []
            checks = []

            def settle(seconds=.15):
                deadline = time.perf_counter() + seconds
                while time.perf_counter() < deadline:
                    app.processEvents()
                    time.sleep(.005)

            def capture(name):
                if screenshots:
                    settle()
                    assert window.grab().save(str(output / f"{name}.png"))

            def measure(name, action):
                app.profile_phase = name
                profiler = cProfile.Profile()
                started = time.perf_counter()
                profiler.enable()
                action()
                returned = time.perf_counter()
                for _ in range(6):
                    app.processEvents()
                profiler.disable()
                app.profile_phase = "between_actions"
                measurement = {"name": name, "action_ms": round((returned - started) * 1000, 2),
                               "with_events_ms": round((time.perf_counter() - started) * 1000, 2)}
                measurements.append(measurement)
                print(json.dumps(measurement), flush=True)
                stream = io.StringIO()
                pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(35)
                (output / f"{name}.txt").write_text(stream.getvalue(), encoding="utf-8")

            try:
                for repeat in range(2):
                    for route in (window.PAGE_SETTINGS, window.PAGE_KNOWLEDGE, window.PAGE_CAPABILITIES):
                        measure(f"{route}-{repeat}", lambda route=route: window.show_product_page(route))
                        if route == window.PAGE_CAPABILITIES:
                            page = window.product_pages[route]
                            assert not page.isWindow()
                            capture(f"{route}-{repeat}")
                            for section in ("mcp", "agents", "skills"):
                                measure(f"{section}-{repeat}", lambda section=section: page.select_section(section))
                        elif repeat == 1:
                            capture(route)
                        measure(f"chat-from-{route}-{repeat}", window.show_conversation_page)
                window.show_product_page(window.PAGE_SETTINGS)
                settings = window.product_pages[window.PAGE_SETTINGS]
                settings.select_initial_page("企业消息")
                settle()
                assert settings._im_gateway_status_timer.isActive()
                settings.select_initial_page("外观与阅读")
                settle()
                assert not settings._im_gateway_status_timer.isActive()
                window.show_conversation_page()
                checks.append("im_status_timer_follows_page_visibility")
                first = window.current_session_id
                measure("new-session", window.create_new_session)
                second = window.current_session_id
                for index, session_id in enumerate((first, second, first)):
                    measure(f"session-{index}", lambda session_id=session_id: window.activate_session(session_id))
                # Include the common case with selected skills and a retained draft.
                available = window._available_session_skills()
                selected = [skill["name"] for skill in available[:2]]
                window.get_current_session().selected_skill_names = selected
                window.refresh_selected_skill_controls()
                window.input_field.setPlainText("切换后需要保留的草稿")
                window.activate_session(second)
                measure("session-with-skills", lambda: window.activate_session(first))
                assert window.input_field.toPlainText() == "切换后需要保留的草稿"
                assert window.get_current_session().selected_skill_names == selected
                checks.append("session_draft_and_selected_skills_preserved")
                if idle_seconds:
                    app.profile_phase = "idle"
                    started = time.perf_counter()
                    max_event_ms = 0
                    while time.perf_counter() - started < idle_seconds:
                        tick = time.perf_counter()
                        app.processEvents()
                        max_event_ms = max(max_event_ms, (time.perf_counter() - tick) * 1000)
                        time.sleep(.005)
                    measurements.append({"name": "idle", "seconds": idle_seconds,
                                         "max_process_events_ms": round(max_event_ms, 2)})
                    for route in (window.PAGE_SETTINGS, window.PAGE_KNOWLEDGE, window.PAGE_CAPABILITIES):
                        measure(f"{route}-after-idle", lambda route=route: window.show_product_page(route))
                        measure(f"chat-from-{route}-after-idle", window.show_conversation_page)
                    measure("session-after-idle", lambda: window.activate_session(second))
                    window.activate_session(first)
                    # Hidden settings status readers must stay idle.
                    settings = window.product_pages[window.PAGE_SETTINGS]
                    assert not settings._im_gateway_status_timer.isActive()
                    from ui.browser_automation_settings import BrowserAutomationSettings
                    assert all(not page.timer.isActive() for page in window.findChildren(BrowserAutomationSettings))
                    checks.append("hidden_status_timers_stopped")
                measure("input", lambda: window.input_field.setPlainText("性能验证消息"))
                with patch.object(window, "process_agent_logic"), patch.object(window, "queue_daemon_connection"):
                    measure("send", window.handle_send)
                assert window.get_current_session().messages[-1]["content"] == "性能验证消息"
                checks.append("send_keeps_user_message")
                capture("sent")
                # Measure UI entry separately from background completion; never hide
                # an unfinished refresh behind the navigation timing.
                deadline = time.monotonic() + 10
                capability_page = window.product_pages[window.PAGE_CAPABILITIES]
                while capability_page._library_refresh_pending and time.monotonic() < deadline:
                    settle(.02)
                assert not capability_page._library_refresh_pending
                checks.append("capability_refresh_completed")
            finally:
                (output / "measurements.json").write_text(json.dumps(measurements, indent=2), encoding="utf-8")
                app.profile_phase = "restore_saved_theme"
                manager.restore_saved_theme(reason="navigation_profile_finished")
                assert manager.repository.load() == saved_theme
                checks.append("theme_preview_did_not_change_saved_configuration")
                environment = {
                    "platform": app.platformName(), "qt_scale_factor": scale,
                    "logical_dpi": window.screen().logicalDotsPerInch(),
                    "device_pixel_ratio": window.devicePixelRatioF(), "dark": dark,
                    "compact": compact, "checks": checks,
                    "slow_ui_events_over_100ms": app.slow_events,
                    "measurement": "cProfile action plus six processEvents calls; excludes model/network latency"
                }
                sent_session_id = window.current_session_id
                storage = window.chat_storage
                app.profile_phase = "shutdown"
                close_test_window(window)
                manager.stop()
                if "send_keeps_user_message" in checks:
                    assert storage.get_messages(sent_session_id)[-1]["content"] == "性能验证消息"
                    checks.append("sent_message_persisted_after_shutdown")
                (output / "environment.json").write_text(json.dumps(environment, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--native", action="store_true")
    parser.add_argument("--scale", type=float, default=1)
    parser.add_argument("--dark", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--idle-seconds", type=float, default=0)
    parser.add_argument("--screenshots", action="store_true")
    try:
        run(**vars(parser.parse_args()))
    except Exception:
        import traceback
        traceback.print_exc(file=sys.__stderr__)
        raise SystemExit(1)
