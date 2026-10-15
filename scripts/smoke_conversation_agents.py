"""Deterministic expert -> main reply -> preview -> edit/save UI acceptance.

Uses a controlled model result, not a live model or a fidelity benchmark.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HTML = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>周末创作工坊</title>
<style>*{box-sizing:border-box}body{margin:0;background:#f7f8f6;color:#25372e;font:16px "Microsoft YaHei",sans-serif}
main{max-width:840px;margin:auto;padding:40px 28px}small{color:#45755a;letter-spacing:2px}h1{font-size:38px;line-height:1.25;margin:18px 0}
p{line-height:1.8;color:#5a6d61}.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin:28px 0}
section{padding:22px;background:white;border:1px solid #dfe5df;border-radius:8px}h2{font-size:19px;margin:0 0 10px}
button{background:#287d53;color:white;padding:12px 22px;font:inherit;border:0;border-radius:6px;cursor:pointer}
@media(max-width:600px){main{padding:24px 20px}h1{font-size:30px}.grid{grid-template-columns:1fr}}
</style></head><body><main><small>周末 · 一起动手创造</small><h1>周末创作工坊</h1>
<p id="editable">用一个下午，把你的想法变成作品。</p><div class="grid"><section><h2>活动安排</h2>
<p>周六 14:00–17:00<br>创意讨论 · 动手制作 · 作品交流</p></section><section><h2>适合谁参加</h2>
<p>想尝试新点子的你<br>无需基础，自带好奇心即可</p></section></div>
<button onclick="document.getElementById('result').textContent='已加入本地意向清单'">加入意向清单</button>
<p id="result">此页面演示本地交互，不提交真实报名。</p></main></body></html>'''


def run(options):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_SCALE_FACTOR"] = str(options.scale)
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    with tempfile.TemporaryDirectory(prefix="ce-") as temporary:
        root = Path(temporary)
        os.environ["APPDATA"] = str(root)
        config = root / "DeepSeekCowork/config.json"
        config.parent.mkdir(parents=True)
        config.write_text("{}", encoding="utf-8")
        from PySide6.QtCore import QCoreApplication, QEvent, Qt
        from PySide6.QtGui import QFontDatabase, QImage, QPainter, QColor, QFont, QFocusEvent
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QMainWindow, QLabel
        from core.conversation_agents import builtin_agent_profiles
        from core.theme import ThemeRuntimeManager
        from core.theme_service import ThemeRepository
        from bs4 import BeautifulSoup
        import main
        app = QApplication([])
        app.setQuitOnLastWindowClosed(False)
        for name in ("msyh.ttc", "segoeui.ttf", "segoeuib.ttf", "consola.ttf", "seguisym.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / name))
        main.initialize_desktop_theme(app)
        output = ROOT / "docs/guides/experts"
        output.mkdir(parents=True, exist_ok=True)
        suffix = f"{options.theme}-{int(options.scale * 100)}"
        with patch.object(main.MainWindow, "showEvent", lambda w, e: QMainWindow.showEvent(w, e)):
            window = main.MainWindow()
            state = window.get_current_session()

            def wait(predicate, timeout=15):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    app.processEvents(); QTest.qWait(20)
                    if predicate():
                        return
                raise AssertionError(f"UI state timed out: session={state.session_status}, preview={window.current_deliverable_path}, edit={window.deliverable_edit_state}")

            def js(view, code):
                values = []
                view.page().runJavaScript(code, values.append)
                wait(lambda: bool(values))
                return values[0]

            def capture(name):
                QTest.qWait(200)
                assert window.grab().save(str(output / f"{name}-{suffix}.png"))

            try:
                theme = ThemeRuntimeManager(app, repository=ThemeRepository(str(root / "theme")))
                app.theme_manager = theme
                window.theme_manager = theme
                theme.themeChanged.connect(window._apply_runtime_theme)
                tokens = {} if options.theme == "light" else {
                    "bg_app": "#14161b", "bg_main": "#191c22", "bg_secondary": "#20242c",
                    "bg_chat": "#14161b", "bg_sidebar": "#191c22", "bg_hover": "#2b303b",
                    "text_primary": "#ecedf1", "text_secondary": "#b7bfcc", "text_tertiary": "#959dad",
                    "border": "#414856", "border_subtle": "#303641", "separator": "#303641",
                    "chat_text": "#ecedf1", "chat_text_muted": "#b7bfcc", "chat_border": "#303641",
                    "primary": "#8c94d7", "primary_soft": "#292d42",
                }
                assert theme.apply_profile({"id": "expert-qa", "name": "专家界面验收", "overrides": {"tokens": tokens}}, preview=True), theme.last_error
                window.resize(1000 if options.scale > 1 else 1240, 800)
                window.skill_manager.load_skills()
                window.skill_manager_ready = True
                window._set_session_workspace(state, str(root), source="project")
                window._apply_workspace_to_ui(str(root), refresh_sidebar=False, remember_workspace=False, persist_default=False)
                window.show()
                window.select_conversation_agent(builtin_agent_profiles()[0])
                window.show_agent_picker()
                capture("picker")
                window.conversation_agent_popover.close()
                # A synthetic reference image makes the attachment path repeatable.
                reference = root / "参考布局.png"
                image = QImage(720, 420, QImage.Format_RGB32); image.fill(QColor("#f7f8f6"))
                painter = QPainter(image); painter.setPen(QColor("#25372e")); painter.setFont(QFont("Microsoft YaHei", 28))
                painter.drawText(40, 95, "周末创作工坊")
                painter.setFont(QFont("Microsoft YaHei", 14)); painter.drawText(40, 145, "用一个下午，把你的想法变成作品。")
                painter.fillRect(40, 190, 300, 160, QColor("white")); painter.fillRect(360, 190, 300, 160, QColor("white"))
                painter.drawText(60, 240, "活动安排"); painter.drawText(380, 240, "适合谁参加"); painter.end()
                assert image.save(str(reference))
                model = {"id": "test-model", "provider_type": "openai", "model_name": "test", "supports_vision": True}
                window._model_id_for_state = lambda s: "test-model"
                window._model_profile_for_state = lambda *a, **k: dict(model)
                window._model_profile_snapshot_for_state = lambda *a, **k: dict(model)
                window._selected_reasoning_effort = lambda *a, **k: ""
                window.queue_daemon_connection = MagicMock(); window.daemon_available = False
                window.process_agent_logic = MagicMock()
                assert window._submit_session_request(state, "参考图片制作活动页面，主色用绿色。", [str(reference)]), [label.text() for label in state.conversation_notice.findChildren(QLabel)] if state.conversation_notice else "submit rejected"
                page = root / ("html-" + state.active_turn_user_message_id[:12] + ".html")
                page.write_text(HTML, encoding="utf-8")
                content = f"页面已完成，支持离线打开和继续编辑。\n\n[打开活动页面](<{page}>)"
                window.handle_llm_response({"content": content, "request_id": state.active_turn_request_id,
                    "generated_messages": [{"id": "qa-result", "role": "assistant", "content": content}]}, state.session_id, state.active_turn_id)
                wait(lambda: not window._session_is_busy(state) and window.deliverable_web_view is not None)
                window.set_context_drawer_width(450 if options.scale == 1 else 360)
                wait(lambda: js(window.deliverable_web_view, "document.querySelector('h1')?.textContent") == "周末创作工坊")
                assert window.agent_picker_btn.text() == "HTML创作"
                assert any(label.text() == "HTML创作" for label in window.findChildren(QLabel, "ConversationAgentName"))
                capture("preview")
                window.begin_deliverable_edit()
                wait(lambda: window.deliverable_edit_state == "ready")
                view = window.deliverable_editor_web_view
                view.setFocus(); QTest.qWait(100)
                target = view.focusProxy() or view
                QCoreApplication.sendEvent(target, QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.OtherFocusReason))
                js(view, """(()=>{const f=document.getElementById('html-frame'),d=f.contentDocument;
                f.focus();f.contentWindow.focus();d.body.focus();const r=d.createRange();r.selectNodeContents(d.getElementById('editable'));
                const s=f.contentWindow.getSelection();s.removeAllRanges();s.addRange(r)})()""")
                QTest.keyClicks(target, "Create something together this weekend.")
                wait(lambda: window.deliverable_edit_dirty)
                assert window.agent_picker_btn.main_btn.width() >= window.agent_picker_btn.main_btn.sizeHint().width()
                assert window.agent_picker_btn.width() >= window.agent_picker_btn.sizeHint().width()
                assert window.agent_picker_btn.main_btn.geometry().right() < window.agent_picker_btn.close_btn.geometry().left()
                capture("edit")
                window.save_deliverable_edit()
                wait(lambda: window.deliverable_edit_state == "ready" and not window.deliverable_edit_dirty)
                saved = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")
                assert saved.find(id="editable").text == "Create something together this weekend."
                assert str(saved.head) == str(BeautifulSoup(HTML, "html.parser").head)
                assert not saved.select('script[src],link[href],img[src^="http"]')
                window.set_deliverable_preview_mode("preview")
                wait(lambda: js(window.deliverable_web_view, "document.getElementById('editable')?.textContent") == "Create something together this weekend.")
                assert js(window.deliverable_web_view, "document.querySelector('button').click();document.getElementById('result').textContent") == "已加入本地意向清单"
                assert window.open_settings("专家"), str(window.current_product_route)
                capture("management")
                print(json.dumps({"result": "PASS", "scale": options.scale, "theme": options.theme,
                    "checks": ["main conversation", "image attachment", "final auto preview", "expert name", "keyboard edit", "save", "offline local interaction"],
                    "model": "controlled result; no live model call"}, ensure_ascii=False))
            finally:
                window._release_deliverable_edit_session(show_preview=False)
                state.live_activity = False; state.session_status = "completed"
                window.close()
                wait(lambda: getattr(window, "_checkpoint_close_ready", False))
                window.file_model.directoryLoaded.disconnect()
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                app.processEvents()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", type=float, default=1)
    parser.add_argument("--theme", choices=["light", "dark"], default="light")
    run(parser.parse_args())
