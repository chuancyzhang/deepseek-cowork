"""Exercise the real Word review entry, keyboard edits, review and repeated save."""
from pathlib import Path
import argparse
import json
import os
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "test")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshots", type=Path)
    options = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="cowork-word-review-") as temporary:
        root = Path(temporary)
        os.environ["APPDATA"] = str(root / "appdata")
        from PySide6.QtCore import QCoreApplication, QEvent, Qt
        from PySide6.QtGui import QFontDatabase, QFocusEvent
        from PySide6.QtWidgets import QApplication, QMainWindow
        from PySide6.QtTest import QTest
        from core.docx_review import WordReviewDocument, tag
        from docx_review_fixture import make_review_docx
        import main
        app = QApplication.instance() or QApplication([])
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "segoeuib.ttf", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / name))
        main.initialize_desktop_theme(app)
        from core.theme import ThemeRuntimeManager
        from core.theme_service import ThemeRepository
        manager = ThemeRuntimeManager(app, ThemeRepository(str(root / "theme")))
        app.theme_manager = manager
        assert manager.apply_repository_state(reason="word_review_smoke"), manager.last_error
        source = root / "文档审阅示例.docx"
        original_parts = make_review_docx(source)
        original = WordReviewDocument(source.read_bytes())
        ids = {name: next(key for key, node in original.revisions.items() if node.get(tag("id")) == value)
               for name, value in (("insert", "10"), ("delete", "11"), ("note", "13"))}
        with patch.object(main.MainWindow, "showEvent", lambda w, e: QMainWindow.showEvent(w, e)):
            window = main.MainWindow(theme_manager=manager)
            manager.themeChanged.connect(window._apply_runtime_theme)
            def wait(predicate, timeout=20):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    app.processEvents(); QTest.qWait(20)
                    if predicate():
                        return
                raise AssertionError("Word editor timed out: " + window.deliverable_edit_state)
            def js(script):
                values = []
                window.deliverable_editor_web_view.page().runJavaScript(script, values.append)
                wait(lambda: bool(values))
                return values[0]
            def edit(before, after):
                view = window.deliverable_editor_web_view
                view.setFocus(); QTest.qWait(50)
                target = view.focusProxy() or view
                QCoreApplication.sendEvent(target, QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.OtherFocusReason))
                selected = js("""(() => {
                    const node = [...document.querySelectorAll('[data-region]')].find(n => n.textContent === %s);
                    if (!node || !node.isContentEditable) return false;
                    node.focus(); const range = document.createRange(); range.selectNodeContents(node);
                    const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range); return true;
                })()""" % json.dumps(before))
                assert selected, before
                QTest.keyClicks(target, after)
                wait(lambda: window.deliverable_edit_dirty)
                assert js("document.activeElement.textContent") == after
            def decide(identifier, button):
                assert js("""(() => {
                    const row = [...document.querySelectorAll('[data-change]')].find(n => n.dataset.change === %s);
                    const button = [...row.querySelectorAll('button')].find(n => n.textContent === %s);
                    button.click(); return true;
                })()""" % (json.dumps(identifier), json.dumps(button)))
            def capture(name):
                if options.screenshots:
                    options.screenshots.mkdir(parents=True, exist_ok=True)
                    QTest.qWait(200)
                    assert window.deliverable_editor_web_view.grab().save(str((options.screenshots / name).resolve()))
            try:
                window.resize(1500, 950); window.show()
                window._apply_workspace_to_ui(str(root), refresh_sidebar=False, remember_workspace=False, persist_default=False)
                window.show_context_drawer(window.RIGHT_TAB_FILES)
                window.set_file_navigator_visible(False, reason="word_review_smoke")
                window.select_deliverable(str(source), render_html=False)
                window.begin_deliverable_edit()
                wait(lambda: window.deliverable_edit_state in {"ready", "error"})
                assert window.deliverable_edit_session.metadata["docx_review"]
                assert js("document.querySelectorAll('.word-section').length") == 3
                assert js("document.querySelectorAll('.word-revision-item').length") == 4
                assert js("document.getElementById('word-revisions').hidden")
                capture("word-review.png")
                js("document.getElementById('toggle-revisions').click()")
                assert not js("document.getElementById('word-revisions').hidden")
                capture("word-review-panel.png")
                js("document.getElementById('toggle-revisions').click()")
                js("document.querySelector('[data-revision].pending').click()")
                assert not js("document.getElementById('word-revisions').hidden")
                edit("正文原文，脚注", "Body updated")
                edit("脚注原文。", "Footnote updated")
                edit("尾注原文。", "Endnote updated")
                js("document.getElementById('undo').click()")
                assert "尾注原文。" in js("document.getElementById('word-document').textContent")
                js("document.getElementById('redo').click()")
                decide(ids["insert"], "接受")
                decide(ids["delete"], "拒绝")
                edit("新增文字", "Accepted insertion edited")
                window.save_deliverable_edit()
                wait(lambda: window.deliverable_edit_state == "ready" and not window.deliverable_edit_dirty)
                saved = WordReviewDocument(source.read_bytes())
                text = " ".join(saved.snapshot["regions"].values())
                for expected in ("Body updated", "Footnote updated", "Endnote updated", "Accepted insertion edited", "删除文字"):
                    assert expected in text, (expected, text)
                assert len(saved.revisions) == 2
                for part, content in original_parts.items():
                    if part not in saved.roots:
                        assert content == saved.parts[part], part
                edit("Body updated", "Body saved twice")
                decide(ids["note"], "拒绝")
                window.save_deliverable_edit()
                wait(lambda: window.deliverable_edit_state == "ready" and not window.deliverable_edit_dirty)
                saved = WordReviewDocument(source.read_bytes())
                assert "Body saved twice" in " ".join(saved.snapshot["regions"].values())
                assert len(saved.revisions) == 1
                window._release_deliverable_edit_session(show_preview=False)
                window.begin_deliverable_edit()
                wait(lambda: window.deliverable_edit_state == "ready")
                assert "Body saved twice" in js("document.getElementById('word-document').textContent")
                window.resize(950, 700); QTest.qWait(100)
                assert js("document.documentElement.scrollWidth <= innerWidth")
                assert js("document.querySelector('.word-layout').scrollHeight > 0")
                capture("word-review-small.png")
                js("document.getElementById('toggle-revisions').click()")
                if js("document.getElementById('word-revisions').hidden"):
                    js("document.getElementById('toggle-revisions').click()")
                QTest.qWait(350)
                assert js("document.getElementById('word-revisions').getBoundingClientRect().top < innerHeight")
                capture("word-review-small-revisions.png")
                print("PASS Word review: body/footnote/endnote keyboard edits, undo/redo, accept/reject, atomic repeated save, original parts preserved, reopen and small window")
            finally:
                window._release_deliverable_edit_session(show_preview=False)
                window._dispose_application_event_filters()
                for attribute in ("chat_save_worker", "runtime_checkpoint_worker"):
                    worker = getattr(window, attribute, None)
                    if worker is not None and worker.isRunning():
                        if hasattr(worker, "request_stop"):
                            worker.request_stop()
                        else:
                            worker.stop_worker(timeout_ms=3000)
                        assert worker.wait(3000), attribute
                window.close(); window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                app.processEvents()


if __name__ == "__main__":
    run()
