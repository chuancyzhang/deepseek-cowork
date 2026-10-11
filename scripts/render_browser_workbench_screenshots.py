"""Isolated representative browser UI captures; review before promoting to docs."""
from pathlib import Path
import argparse
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
storage = tempfile.TemporaryDirectory(prefix="cowork-browser-qa-")
os.environ["APPDATA"] = str(Path(storage.name) / "appdata")
config_dir = Path(os.environ["APPDATA"]) / "DeepSeekCowork"
config_dir.mkdir(parents=True)
(config_dir / "config.json").write_text("{}", encoding="utf-8")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtTest import QTest
from PySide6.QtWebEngineWidgets import QWebEngineView
import main


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/download":
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", 'attachment; filename="result.txt"')
            self.end_headers(); self.wfile.write(b"browser download test")
            return
        body = '''<!doctype html><meta charset="utf-8"><title>项目进展</title>
        <style>body{font:16px 'Microsoft YaHei';padding:32px;color:#202124;background:#fff}
        h1{font-size:28px}label{display:block;margin:24px 0 8px}input{padding:10px;width:80%;border:1px solid #ddd;border-radius:5px}
        button{padding:9px 18px;background:#fff;border:1px solid #ccc;margin-top:16px}</style>
        <h1>项目进展</h1><p>记录本周完成的工作，与团队确认下一步。</p>
        <label>本周进展</label><input value="已完成方案评审，正在检查交付文件。">
        <p><button>保存更新</button></p>'''.encode("utf-8")
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers(); self.wfile.write(body)
    def log_message(self, *_args): pass


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(ROOT / ".tmp/browser-qa"))
    options = parser.parse_args()
    output = Path(options.output); output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    for filename in ("msyh.ttc", "consola.ttf", "consolab.ttf", "segoeui.ttf", "segoeuib.ttf"):
        font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename
        if font.exists(): QFontDatabase.addApplicationFont(str(font))
    main.initialize_desktop_theme(app)
    from core.theme import ThemeRuntimeManager, DesignTokens, default_design_tokens
    from core.theme_service import ThemeRepository
    theme = ThemeRuntimeManager(app, ThemeRepository(str(Path(storage.name) / "themes")))
    app.theme_manager = theme
    theme.apply_repository_state(reason="browser_qa")
    original_show = main.MainWindow.showEvent
    main.MainWindow.showEvent = lambda window, event: QMainWindow.showEvent(window, event)
    window = main.MainWindow(theme_manager=theme)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    def events(delay=150):
        app.processEvents(); QTest.qWait(delay); app.processEvents()
    def wait(predicate, timeout=15):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            events(20)
            if predicate(): return
        raise AssertionError("capture state did not load")
    scale = os.environ.get("QT_SCALE_FACTOR", "1")
    def capture(name, width=1440, height=900):
        window.resize(width, height); events(200)
        path = output / f"{name}-{width}-{scale}.png"
        assert window.grab().save(str(path), "PNG")
    try:
        workspace = Path(storage.name) / "项目文件"; workspace.mkdir()
        source = workspace / "报告检查.py"
        source.write_text('# 检查交付文件\nfrom pathlib import Path\n\nfor file in Path(".").glob("*.pdf"):\n    print(file.name, file.stat().st_size)\n', encoding="utf-8")
        window._apply_workspace_to_ui(str(workspace), refresh_sidebar=False, remember_workspace=False, persist_default=False)
        window.resize(1440, 900); window.show()
        window.show_context_drawer(window.RIGHT_TAB_FILES)
        window.set_file_navigator_visible(False, reason="browser_qa")
        window.add_chat_bubble("User", "完善报告，并检查交付文件。", animate=False)
        window.add_chat_bubble("Agent", "文件、网页和终端可以在右侧继续查看。你可以随时接管网页或终端。", animate=False)
        browser = window.browser_workbench
        browser.new_tab(); events()
        capture("browser-start")
        capture("browser-start", 1000, 720)
        window.select_deliverable(str(source), render_html=True)
        file_tab = browser.active_tab()
        capture("browser-files")
        web_tab = browser.open_web(window.current_session_id, f"http://127.0.0.1:{server.server_port}/")
        terminal_tab = browser.open_terminal(window.current_session_id, "bash", str(workspace))
        wait(lambda: browser.panes[web_tab.id].document_ready)
        wait(lambda: browser.panes[terminal_tab.id].terminal.status == "running")
        browser.activate(web_tab.id)
        browser.store.set_owner(web_tab.session_id, web_tab.id, "agent")
        browser.panes[web_tab.id].refresh_control()
        capture("browser-web")
        capture("browser-web", 1000, 720)
        browser.activate(terminal_tab.id)
        terminal = browser.panes[terminal_tab.id].terminal
        terminal.write("printf '\\033[2J\\033[H'; printf '正在检查交付文件…\\n报告.pdf  42816 bytes\\n检查完成。\\n'\r")
        wait(lambda: "检查完成" in terminal.read()["output"])
        capture("browser-terminal")
        capture("browser-terminal", 1000, 720)
        browser.activate(file_tab.id)
        window.begin_deliverable_edit()
        wait(lambda: window.deliverable_edit_state == "ready")
        window.deliverable_text_editor.appendPlainText("\n# 补充：检查空文件")
        source.write_text("# Agent 已保存新的检查逻辑\n", encoding="utf-8")
        from ui.file_refresh import refresh_file
        refresh_file(window)
        capture("browser-conflict")
        capture("browser-conflict", 1000, 720)
        saved_color = DesignTokens.bg_main
        theme.repository.write_preview(name="浏览器主题验收", overrides={"tokens": {"primary": "#39734d", "primary_hover": "#2d603e"}}, default_tokens=default_design_tokens())
        assert theme.apply_repository_state(reason="browser_preview")
        capture("browser-theme-preview", 1000, 720)
        assert theme.restore_saved_theme()
        assert DesignTokens.bg_main == saved_color
        window._release_deliverable_edit_session(show_preview=False)
        active_before = browser.active_tab().id
        count_before = len(browser.store.list(window.current_session_id))
        browser.panes[web_tab.id].page.runJavaScript("window.open('/popup')")
        wait(lambda: len(browser.store.list(window.current_session_id)) > count_before)
        assert browser.active_tab().id == active_before
        popup = browser.store.list(window.current_session_id)[-1]
        assert popup.kind == "web" and popup.session_id == web_tab.session_id
        download_path = workspace / "download.txt"
        with patch("ui.browser_workbench.QFileDialog.getSaveFileName", return_value=(str(download_path), "")):
            browser.panes[web_tab.id].page.runJavaScript("location.href='/download'")
            wait(lambda: browser.downloads and browser.downloads[-1]["status"] == "已完成")
        assert download_path.read_bytes() == b"browser download test"
        assert browser.downloads[-1]["session_id"] == web_tab.session_id
        print("popup/download preserve chat and active tab: PASS", flush=True)
        print(f"browser UI captures and theme cancellation passed: scale={scale}", flush=True)
    finally:
        main.MainWindow.showEvent = original_show
        window._release_deliverable_edit_session(show_preview=False)
        window.close(); window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        server.shutdown(); server.server_close()
        # SQLite services may still finish closing; remove only this isolated run.
        storage.cleanup()


if __name__ == "__main__": run()
