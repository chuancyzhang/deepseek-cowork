"""Offline browser, worksheet, PDF and real ConPTY acceptance smoke.

Run with the repository Python. QT_SCALE_FACTOR can be 1, 1.25 or 1.5.
"""
from pathlib import Path
import os
import sys
import tempfile
import threading
import time
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PySide6.QtCore import QCoreApplication, QEvent, QUrl
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineCore import QWebEngineProfile
from core.workbench import WorkbenchStore, WorkbenchBridge, WorkbenchRequest
from core.workbench_terminal import TerminalSession, terminal_profiles
from ui.browser_panes import BrowserPane, TerminalPane
from ui.document_preview import DocumentPreview


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        data = b'''<!doctype html><meta charset="utf-8"><title>Workbench smoke</title>
        <h1>Shared page</h1><form onsubmit="event.preventDefault();document.querySelector('output').textContent='Saved'">
        <input aria-label="Name"><button>Save</button></form>
        <output></output><div style="height:2000px">Scrolling text</div>'''
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        if self.path == "/login":
            self.send_header("Set-Cookie", "workbench_login=shared; Max-Age=3600; Path=/; SameSite=Lax")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        pass


def main():
    app = QApplication.instance() or QApplication([])
    def wait(predicate, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            time.sleep(.01)
        detail = terminal.read() if terminal is not None else {}
        raise AssertionError("smoke condition timed out: " + repr(detail))

    def js(page, source, world=0):
        values = []
        page.runJavaScript(source, world, values.append)
        wait(lambda: bool(values))
        return values[0]

    def request(operation, **arguments):
        return WorkbenchRequest("smoke", "run", operation, arguments, {}, time.monotonic() + 15)

    store, bridge = WorkbenchStore(), WorkbenchBridge()
    browser_storage = tempfile.TemporaryDirectory()
    def make_profile():
        result = QWebEngineProfile("workbench-smoke")
        result.setPersistentStoragePath(str(Path(browser_storage.name) / "storage"))
        result.setCachePath(str(Path(browser_storage.name) / "cache"))
        return result
    profile = make_profile()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    pane = background = doc = terminal_pane = terminal = None
    try:
        tab = store.open("smoke", "web", url=f"http://127.0.0.1:{server.server_port}/")
        pane = BrowserPane(tab, store, bridge, profile)
        pane.resize(840, 660); pane.show()
        wait(lambda: pane.document_ready)
        settled = time.monotonic() + .15
        wait(lambda: time.monotonic() > settled)
        observed = request("observe")
        pane.observe(observed, observed.finish)
        wait(observed.event.is_set)
        assert observed.result.get("ok"), observed.result
        name = next(el for el in observed.result["elements"] if el["name"] == "Name")
        store.set_owner("smoke", tab.id, "agent"); pane.refresh_control()
        fill = request("browser_action", page_version=observed.result["page_version"], action="fill", element_id=name["id"], text="中文接力")
        fill.revision = tab.control_revision
        pane.act(fill, fill.finish); wait(fill.event.is_set)
        assert fill.result.get("ok"), (fill.result, observed.result, js(pane.page, "JSON.stringify(window.coworkObserve())", 1))
        assert js(pane.page, "document.querySelector('input').value") == "中文接力"
        stale = request("browser_action", page_version=observed.result["page_version"], action="click", element_id=name["id"])
        stale.revision = tab.control_revision
        pane.act(stale, stale.finish); wait(stale.event.is_set)
        assert stale.result.get("status") == "stale_page", stale.result
        pane.take_over()
        assert tab.owner == "human"
        assert js(pane.page, "typeof qt") == "undefined", "online page acquired a native bridge"
        print("browser observe/fill/stale/control/profile: PASS", flush=True)
        pane.give_back()
        store.set_owner("smoke", tab.id, "agent"); pane.refresh_control()
        observed = request("observe"); pane.observe(observed, observed.finish); wait(observed.event.is_set)
        save = next(el for el in observed.result["elements"] if el["name"] == "Save")
        queued = request("browser_action", page_version=observed.result["page_version"], action="click", element_id=save["id"])
        queued.revision = tab.control_revision
        pane.act(queued, queued.finish)
        pane.take_over()  # Before the queued validation returns to the GUI thread.
        wait(queued.event.is_set)
        assert not queued.result["ok"], queued.result
        assert js(pane.page, "document.querySelector('output').textContent") == ""
        print("takeover cancels queued webpage submission: PASS", flush=True)
        pane.give_back()
        store.set_owner("smoke", tab.id, "agent"); pane.refresh_control()
        observed = request("observe"); pane.observe(observed, observed.finish); wait(observed.event.is_set)
        save = next(el for el in observed.result["elements"] if el["name"] == "Save")
        submitted = request("browser_action", page_version=observed.result["page_version"], action="click", element_id=save["id"])
        submitted.revision = tab.control_revision; pane.act(submitted, submitted.finish); wait(submitted.event.is_set)
        assert submitted.result["ok"], submitted.result
        # Native input is dispatched asynchronously to Chromium. Confirm the
        # effect without replaying the click while its event is still queued.
        wait(lambda: js(pane.page, "document.querySelector('output').textContent") == "Saved", timeout=3)
        assert js(pane.page, "document.querySelector('output').textContent") == "Saved", (
            submitted.result, observed.result.get("viewport"), pane.view.size(), pane.view.devicePixelRatioF(),
            js(pane.page, "JSON.stringify({scale:devicePixelRatio,rect:document.querySelector('button').getBoundingClientRect().toJSON(),active:document.activeElement.tagName})"))
        js(pane.page, "document.querySelector('input').focus();document.querySelector('input').setSelectionRange(4,4)")
        observed = request("observe"); pane.observe(observed, observed.finish); wait(observed.event.is_set)
        key = request("browser_action", page_version=observed.result["page_version"], action="key", key="Backspace")
        key.revision = tab.control_revision; pane.act(key, key.finish); wait(key.event.is_set)
        assert key.result["ok"], key.result
        wait(lambda: js(pane.page, "document.querySelector('input').value") == "中文接", timeout=3)
        actual = js(pane.page, "document.querySelector('input').value")
        assert actual == "中文接", (actual, js(pane.page, "JSON.stringify({tag:document.activeElement.tagName,focus:document.hasFocus()})"))
        print("agent form submission and native key respect input ownership: PASS", flush=True)
        js(pane.page, "document.querySelector('input').readOnly=true")
        observed = request("observe"); pane.observe(observed, observed.finish); wait(observed.event.is_set)
        name = next(el for el in observed.result["elements"] if el["name"] == "Name")
        readonly = request("browser_action", page_version=observed.result["page_version"], action="fill", element_id=name["id"], text="must not write")
        readonly.revision = tab.control_revision; pane.act(readonly, readonly.finish); wait(readonly.event.is_set)
        assert readonly.result.get("status") == "unsupported", readonly.result
        js(pane.page, "document.querySelector('input').readOnly=false;document.querySelector('input').oninput=event=>setTimeout(()=>event.target.value='',0)")
        observed = request("observe"); pane.observe(observed, observed.finish); wait(observed.event.is_set)
        name = next(el for el in observed.result["elements"] if el["name"] == "Name")
        reset = request("browser_action", page_version=observed.result["page_version"], action="fill", element_id=name["id"], text="rejected by page")
        reset.revision = tab.control_revision; pane.act(reset, reset.finish); wait(reset.event.is_set)
        assert reset.result.get("status") == "fill_not_verified", reset.result
        js(pane.page, "let overlay=document.createElement('div');overlay.style='position:fixed;inset:0;z-index:9999';document.body.append(overlay)")
        observed = request("observe"); pane.observe(observed, observed.finish); wait(observed.event.is_set)
        save = next(el for el in observed.result["elements"] if el["name"] == "Save")
        covered = request("browser_action", page_version=observed.result["page_version"], action="click", element_id=save["id"])
        covered.revision = tab.control_revision; pane.act(covered, covered.finish); wait(covered.event.is_set)
        assert covered.result.get("status") == "element_obscured", covered.result
        print("readonly/reverted fill/covered native target: PASS", flush=True)
        pane.take_over(); pane.give_back()
        pane.view.setUrl(QUrl(tab.url + "login"))
        wait(lambda: pane.document_ready and pane.tab.url.endswith('/login'))
        wait(lambda: 'workbench_login=shared' in str(js(pane.page, 'document.cookie')))
        background_tab = store.open("smoke", "web", url=f"http://127.0.0.1:{server.server_port}/")
        background = BrowserPane(background_tab, store, bridge, profile)
        background.resize(840, 660)
        background.park()
        wait(lambda: background.document_ready)
        settled = time.monotonic() + .15
        wait(lambda: time.monotonic() > settled)
        image = background.view.grab().toImage()
        colors = {image.pixelColor(x, y).name() for x in range(0, image.width(), 3) for y in range(0, min(image.height(), 150), 3)}
        assert len(colors) > 3, "Background webpage screenshot is blank"
        assert 'workbench_login=shared' in js(background.page, 'document.cookie')
        print("background webpage screenshot without focus: PASS", flush=True)

        if os.name == "nt":
            shell = next(p for p in terminal_profiles() if p["id"] == "bash")
            terminal_tab = store.open("smoke", "terminal", cwd=str(ROOT), shell="bash")
            terminal = TerminalSession("smoke", terminal_tab.id, shell, str(ROOT))
            terminal_pane = TerminalPane(terminal_tab, store, bridge, terminal)
            terminal_pane.resize(840, 660); terminal_pane.show(); terminal.start()
            wait(lambda: terminal.status != "starting")
            assert terminal.status == "running", terminal.error
            wait(lambda: terminal_pane.ready)
            terminal.write("printf '\\u4e2d\\u6587\\n'; echo WORKBENCH_OK\r")
            wait(lambda: "中文" in terminal.read()["output"])
            terminal.resize(32, 100)
            terminal.write("echo SLEEP_STARTED; sleep 20\r")
            wait(lambda: terminal.read()["output"].count("SLEEP_STARTED") >= 2)
            # The echo precedes creation of the foreground process. Allow Bash
            # to hand the console to sleep before testing its interruption.
            foreground_ready = time.monotonic() + .5
            wait(lambda: time.monotonic() >= foreground_ready)
            prompt_at = terminal.read()["cursor"]
            terminal.write("\x03")
            wait(lambda: "$ " in terminal.read(prompt_at)["output"])
            terminal.write("echo INTERRUPT_OK\r")
            wait(lambda: "INTERRUPT_OK" in terminal.read()["output"])
            terminal.close(); terminal.thread.join(5)
            assert not terminal.thread.is_alive(), "PTY reader did not stop"
            print("terminal UTF-8/resize/interrupt/cleanup: PASS", flush=True)

        with tempfile.TemporaryDirectory() as directory:
            from openpyxl import Workbook
            from pypdf import PdfWriter
            root = Path(directory)
            writer = PdfWriter(); writer.add_blank_page(600, 800)
            with open(root / "test.pdf", "wb") as stream: writer.write(stream)
            book = Workbook(); book.active.append(["中文工作表", 123]); book.create_sheet("第二页"); book.save(root / "test.xlsx")
            doc = DocumentPreview(); doc.resize(840, 660); doc.show()
            doc.open(str(root / "test.xlsx"), "sheet")
            wait(lambda: doc.sheet_view is not None and doc.stack.currentWidget() is doc.sheet_view)
            assert doc.sheet_page.webChannel() is not None
            assert not hasattr(doc.sheet_channel.registeredObjects()["deliverableEditorBridge"], "saveState")
            doc.open(str(root / "test.pdf"))
            wait(lambda: doc.pdf_view is not None and doc.stack.currentWidget() is doc.pdf_view)
            loaded = []; doc.pdf_view.loadFinished.connect(loaded.append)
            wait(lambda: bool(loaded))
            assert loaded[-1], doc.status.text()
            source = root / "test.pdf"
            source.write_bytes(source.read_bytes())
            source.unlink()
            assert doc.pdf_view.url().toLocalFile() != str(source)
            print("worksheet readonly/PDF/source release: PASS", flush=True)
        for current in (pane, background):
            current.close_pane(); current.deleteLater()
        pane = background = None
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        profile.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        profile = make_profile()
        tab = store.open("another-chat", "web", url=f"http://127.0.0.1:{server.server_port}/")
        pane = BrowserPane(tab, store, bridge, profile); pane.resize(840, 660); pane.show()
        wait(lambda: pane.document_ready)
        assert 'workbench_login=shared' in js(pane.page, 'document.cookie')
        print("login shared across chats and retained after profile recreation: PASS", flush=True)
    finally:
        if doc: doc.dispose(); doc.deleteLater()
        if terminal_pane: terminal_pane.close_pane(); terminal_pane.deleteLater()
        if terminal: terminal.close(); terminal.thread.join(5)
        if pane: pane.close_pane(); pane.deleteLater()
        if background: background.close_pane(); background.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        profile.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        server.shutdown(); server.server_close()
        browser_storage.cleanup()


if __name__ == "__main__":
    main()
