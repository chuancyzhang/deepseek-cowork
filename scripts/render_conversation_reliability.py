"""Render real failure/settings widgets with isolated data and no model calls."""

from qa_support import prepare_qa_directory
QA_ROOT = prepare_qa_directory(__file__)
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "test")]


def render(output, scale, dark, narrow):
    temporary_root = ROOT / ".tmp" / "conversation-reliability"
    temporary_root.mkdir(parents=True, exist_ok=True)
    os.environ.update(QT_QPA_PLATFORM="offscreen", QT_SCALE_FACTOR=str(scale),
                      COWORK_EXTERNAL_BACKGROUND_SERVICES="0", TEMP=str(temporary_root), TMP=str(temporary_root))
    with tempfile.TemporaryDirectory(dir=temporary_root) as temporary:
        os.environ.update(APPDATA=temporary, LOCALAPPDATA=temporary)
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtTest import QTest
        from core.config_manager import ConfigManager
        from core.theme import ThemeRuntimeManager
        from core.theme_presets import builtin_theme
        from core.theme_service import ThemeRepository
        from main import MainWindow
        from ui.long_context import ContextRolloverNotice
        from ui_window_cleanup import close_test_window

        app = QApplication([])
        for font in ("msyh.ttc", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["SystemRoot"], "Fonts", font)))
        with patch("core.config_manager.get_app_data_dir", return_value=temporary), \
             patch("core.config_manager.get_base_dir", return_value=temporary):
            config = ConfigManager()
        config.set_model_channels([{"channel_id": "service", "display_name": "示例服务", "provider_type": "openai",
            "base_url": "https://example.test/v1", "api_key": "example-key", "models": [{
                "id": "model", "model_name": "gpt-6-astra", "api_protocol": "responses", "supports_vision": True,
                "supports_image_generation": True, "discovery": {"group_restrictions": {"allow_image_generation": False}}}]}], "model")
        repository = ThemeRepository(temporary)
        theme = ThemeRuntimeManager(app, repository)
        app.theme_manager = theme
        with patch.object(MainWindow, "start_background_services", lambda self: None):
            window = MainWindow(config_manager=config, theme_manager=theme)
        window._model_onboarding_shown = True
        window.resize(760 if narrow else 1280, 760 if narrow else 900)
        saved_path = Path(repository.store_path)
        saved = saved_path.read_bytes() if saved_path.exists() else None
        assert theme.apply_profile(builtin_theme("default", dark=dark), preview=True), theme.last_error
        output.mkdir(parents=True, exist_ok=True)
        suffix = f"{'dark' if dark else 'light'}-{int(scale * 100)}{'-narrow' if narrow else ''}"

        def capture(name):
            QTest.qWait(180)
            assert window.grab().save(str(output / f"{name}-{suffix}.png"))

        try:
            window.show()
            state = window.get_current_session()
            window._retire_session_empty_state(state, reason="reliability_screenshot")
            window.add_chat_bubble("User", "请继续整理报告，并保留已完成内容。", animate=False)
            bubble = window.add_chat_bubble("Agent", "已完成资料核对，正在整理下一部分。\n\n    已保留的正文与缩进", animate=False)
            state.failure_notice_anchor = bubble
            state.active_run_retry_context = {"source_run_id": "preview", "source_user_message_id": "user"}
            for name, error in (
                ("final-conflict", "服务返回的最终正文与已接收内容不一致，已有内容已保留，请重试。"),
                ("image-permission", "403 Image generation is not enabled for this group")):
                summary = window._run_failure_presentation(error, False, 0, 0)[1]
                notice = window._show_retryable_run_failure(state, summary, "preview")
                state.chat_scroll.ensureWidgetVisible(notice)
                capture(name)
                state.chat_layout.removeWidget(notice)
                notice.hide()
                notice.deleteLater()
                state.conversation_notice = None
            compression = ContextRolloverNotice(lambda: None)
            compression.set_event({"phase": "failed", "error": "上下文压缩输出达到上限，未保存这次检查点。请重试压缩。"})
            state.chat_layout.insertWidget(max(0, state.chat_layout.count() - 1), compression)
            capture("compression-failure")
            window.open_settings("模型与服务")
            page = window.product_pages[window.PAGE_SETTINGS]
            manager = page.model_channel_manager
            manager.open_service("service")
            manager.editors[0].open_model_form("model")
            form = manager.editors[0].model_forms["model"]
            QTest.qWait(150)
            form.image_generation_check.setFocus()
            scroll = page._settings_pages[page._page_labels.index("模型与服务")]
            scroll.ensureWidgetVisible(form.image_generation_check)
            assert form.isVisible()
            assert scroll.horizontalScrollBar().maximum() == 0
            capture("model-permission")
            assert form.image_generation_check.isChecked() and form.vision_check.isChecked()
            assert "未开通图片生成" in form.discovery_warning.text()
            assert (saved_path.read_bytes() if saved_path.exists() else None) == saved
            (output / f"verification-{suffix}.json").write_text(json.dumps({
                "window": [window.width(), window.height()], "device_pixel_ratio": window.devicePixelRatioF(),
                "theme_preview_persisted": False, "model_requests": False,
                "image_generation_unchanged": True}, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            theme.restore_saved_theme(reason="reliability_preview_finished")
            theme.stop()
            close_test_window(window)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / ".tmp" / "conversation-reliability" / "screenshots")
    parser.add_argument("--scale", type=float, default=1)
    parser.add_argument("--dark", action="store_true")
    parser.add_argument("--narrow", action="store_true")
    args = parser.parse_args()
    render(args.output.resolve(), args.scale, args.dark, args.narrow)
