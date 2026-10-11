"""Verify the normal HTML edit entry, rendered CSS, keyboard edits and safe save."""
from pathlib import Path
import argparse
import json
import os
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")


def run():
    import faulthandler
    faulthandler.dump_traceback_later(45)
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshot", type=Path)
    options = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="cowork-html-visual-") as temporary:
        root = Path(temporary)
        os.environ["APPDATA"] = str(root / "appdata")
        config = root / "appdata/DeepSeekCowork/config.json"
        config.parent.mkdir(parents=True)
        config.write_text('{"html_editor_mode":"source"}', encoding="utf-8")
        from PySide6.QtCore import QCoreApplication, QEvent, Qt
        from PySide6.QtGui import QFontDatabase, QFocusEvent
        from PySide6.QtWidgets import QApplication, QMainWindow
        from PySide6.QtTest import QTest
        from bs4 import BeautifulSoup
        import main
        app = QApplication.instance() or QApplication([])
        for name in ("msyh.ttc", "segoeui.ttf", "segoeuib.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / name))
        main.initialize_desktop_theme(app)
        source = root / "练习页面.html"
        assets = root / "assets"; assets.mkdir()
        (assets / "styles.css").write_text(
            "body{font:16px 'Microsoft YaHei';margin:0;padding:28px;background:#f7f8fa;color:#30343b}"
            "h1{font-size:28px;color:rgb(20,80,120)}.stats{display:grid;grid-template-columns:1fr 1fr;gap:16px}"
            "section{background:white;padding:20px;border:1px solid #e5e7eb;border-radius:6px}"
            "button{padding:8px 16px;border:1px solid #ccc;background:white}", encoding="utf-8")
        source.write_text('''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>期货从业双科刷题工具</title><base href="assets/"><link rel="stylesheet" href="styles.css">
<script>window.shouldNotRun = true; document.body.innerHTML = "wrong";</script></head>
<body><h1>期货从业双科刷题工具</h1><p>在页面上直接选择文字，修改学习安排。</p>
<div class="stats"><section><h2>今日计划</h2><p id="editable">完成 20 道练习题</p></section>
<section><h2>学习进度</h2><p>基础知识 · 法律法规</p></section></div>
<p><button onclick="startPractice()">开始练习</button></p>
<script src="app.js"></script></body></html>''', encoding="utf-8")
        original = BeautifulSoup(source.read_text(encoding="utf-8"), "html.parser")
        with patch.object(main.MainWindow, "showEvent", lambda w, e: QMainWindow.showEvent(w, e)):
            window = main.MainWindow()
            def wait(predicate, timeout=20):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    app.processEvents(); QTest.qWait(20)
                    if predicate(): return
                raise AssertionError("HTML editor state timed out: " + window.deliverable_edit_state)
            def js(script):
                values = []
                window.deliverable_editor_web_view.page().runJavaScript(script, values.append)
                wait(lambda: bool(values))
                return values[0]
            try:
                print("HTML smoke: opening workspace", flush=True)
                window.resize(1200, 860); window.show()
                window._apply_workspace_to_ui(str(root), refresh_sidebar=False, remember_workspace=False, persist_default=False)
                window.show_context_drawer(window.RIGHT_TAB_FILES)
                window.set_file_navigator_visible(False, reason="html_visual_smoke")
                window.select_deliverable(str(source), render_html=False)
                print("HTML smoke: entering visual editor", flush=True)
                window.begin_deliverable_edit()
                wait(lambda: window.deliverable_edit_state in {"ready", "error"})
                assert window.deliverable_edit_session.descriptor.kind == "html"
                assert window.preview_stack.currentWidget() is window.deliverable_editor_web_view
                print("HTML smoke: checking styles and keyboard input", flush=True)
                probe = json.loads(js('''JSON.stringify((() => {
                  const f=document.getElementById('html-frame'), d=f.contentDocument;
                  return {color:f.contentWindow.getComputedStyle(d.querySelector('h1')).color,
                    grid:f.contentWindow.getComputedStyle(d.querySelector('.stats')).display,
                    editable:d.body.isContentEditable, executed:!!f.contentWindow.shouldNotRun,
                    title:d.querySelector('h1').textContent}; })())'''))
                assert probe == {"color": "rgb(20, 80, 120)", "grid": "grid", "editable": True,
                                 "executed": False, "title": "期货从业双科刷题工具"}, probe
                view = window.deliverable_editor_web_view
                view.setFocus(); QTest.qWait(100)
                target = view.focusProxy() or view
                QCoreApplication.sendEvent(target, QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.OtherFocusReason))
                js('''(() => {const f=document.getElementById('html-frame'),d=f.contentDocument;
                  f.focus(); f.contentWindow.focus(); d.body.focus(); const r=d.createRange();
                  r.selectNodeContents(d.getElementById('editable'));
                  const s=f.contentWindow.getSelection();s.removeAllRanges();s.addRange(r);})()''')
                QTest.keyClicks(target, "Updated study plan")
                wait(lambda: window.deliverable_edit_dirty)
                assert js("document.getElementById('html-frame').contentDocument.getElementById('editable').textContent") == "Updated study plan"
                if options.screenshot:
                    QTest.qWait(250)
                    options.screenshot.parent.mkdir(parents=True, exist_ok=True)
                    assert view.grab().save(str(options.screenshot.resolve()))
                window.save_deliverable_edit()
                print("HTML smoke: saving", flush=True)
                wait(lambda: window.deliverable_edit_state == "ready" and not window.deliverable_edit_dirty)
                saved = BeautifulSoup(source.read_text(encoding="utf-8"), "html.parser")
                assert str(saved.head) == str(original.head)
                assert saved.find("script", src="app.js") is not None
                assert saved.button["onclick"] == "startPractice()"
                assert saved.find(id="editable").text == "Updated study plan"
                assert "data-cowork-" not in source.read_text(encoding="utf-8")
                # Source mode remains an explicit choice; returning to the normal
                # edit entry opens the visual editor even after a source session.
                window._release_deliverable_edit_session(show_preview=False)
                window.begin_deliverable_edit(editor_mode="source")
                wait(lambda: window.deliverable_edit_state == "ready")
                assert window.deliverable_edit_session.descriptor.kind == "text"
                window._release_deliverable_edit_session(show_preview=False)
                window.begin_deliverable_edit()
                wait(lambda: window.deliverable_edit_state == "ready")
                assert window.deliverable_edit_session.descriptor.kind == "html"
                window._release_deliverable_edit_session(show_preview=False)
                legacy = root / "legacy.html"
                legacy.write_bytes("<html><body>中文旧编码</body></html>".encode("gb18030"))
                window.select_deliverable(str(legacy), render_html=False)
                with patch.object(window, "_choose_deliverable_encoding", return_value="gb18030"):
                    window.begin_deliverable_edit(editor_mode="source")
                    wait(lambda: window.deliverable_edit_state == "ready")
                assert window.deliverable_edit_session.descriptor.kind == "text"
                assert "中文旧编码" in window.deliverable_text_editor.toPlainText()
                print("PASS HTML visual default, local CSS/base, keyboard edit, atomic save, scripts/head preserved, explicit source mode and encoding retry")
            finally:
                window._release_deliverable_edit_session(show_preview=False)
                window._dispose_application_event_filters()
                window.close(); window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                app.processEvents()
                faulthandler.cancel_dump_traceback_later()


if __name__ == "__main__":
    run()
