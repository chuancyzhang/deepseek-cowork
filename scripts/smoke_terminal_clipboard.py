"""Exercise the real WebEngine/xterm clipboard path with native Qt input."""
import json
import os
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu
from core.workbench import WorkbenchBridge, WorkbenchError, WorkbenchStore
from ui.browser_panes import TerminalPane


class RecordingTerminal:
    status = "running"
    error = ""
    exit_code = None

    def __init__(self):
        self.inputs = []

    def write(self, text):
        if self.status != "running":
            raise WorkbenchError("terminal_not_running", "终端未运行，请检查状态或重新启动。")
        self.inputs.append(text)

    def read(self, *_args):
        return {"output": "", "cursor": 0, "truncated": False}

    def resize(self, *_args): pass
    def close(self): pass


class TerminalClipboardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.clipboard = cls.app.clipboard().text()
        for name in ("msyh.ttc", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name))

    @classmethod
    def tearDownClass(cls):
        cls.app.clipboard().setText(cls.clipboard)

    def wait(self, predicate, timeout=10):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate(): return
            QTest.qWait(10)
        self.fail("terminal condition timed out")

    def js(self, source):
        values = []
        self.pane.page.runJavaScript(source, values.append)
        self.wait(lambda: bool(values))
        return values[0]

    def setUp(self):
        self.store = WorkbenchStore()
        self.tab = self.store.open("clipboard-test", "terminal", cwd=str(ROOT), shell="pwsh")
        self.terminal = RecordingTerminal()
        self.pane = TerminalPane(self.tab, self.store, WorkbenchBridge(), self.terminal)
        self.pane.resize(650, 340)
        self.pane.show()
        self.wait(lambda: self.pane.ready)
        self.js('window.coworkTerminal.write("CopyOutput 中文命令和输出\\r\\n", 1)')
        self.wait(lambda: self.pane.cursor == 1)
        self.focus()

    def tearDown(self):
        self.pane.close_pane()
        self.pane.close()
        self.pane.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def focus(self):
        self.pane.activateWindow()
        self.pane.view.setFocus()
        self.js("window.coworkTerminal.focus()")
        QTest.qWait(80)

    def key(self, key, modifiers):
        self.focus()
        QTest.keyClick(self.pane.view.focusProxy() or self.pane.view, key, modifiers)
        QTest.qWait(150)

    def test_copy_selection_and_interrupt(self):
        # Use a mouse selection in the canvas, not the browser DOM selection.
        rect = json.loads(self.js("JSON.stringify((()=>{const r=document.querySelector('.xterm-screen').getBoundingClientRect();return {x:r.x,y:r.y}})())"))
        position = QPoint(int(rect["x"]) + 1, int(rect["y"]) + 8)
        end = QPoint(int(rect["x"]) + 77, int(rect["y"]) + 8)
        QTest.mousePress(self.pane.view.focusProxy(), Qt.LeftButton, pos=position)
        QTest.mouseMove(self.pane.view.focusProxy(), end, 50)
        QTest.qWait(100)
        QTest.mouseRelease(self.pane.view.focusProxy(), Qt.LeftButton, pos=end)
        self.wait(lambda: self.pane.copy_button.isEnabled())
        for modifiers in (Qt.ControlModifier, Qt.ControlModifier | Qt.ShiftModifier):
            self.app.clipboard().setText("sentinel")
            self.key(Qt.Key_C, modifiers)
            self.assertEqual(self.app.clipboard().text(), "CopyOutput")
        self.assertEqual(self.terminal.inputs, [])
        self.js("window.coworkTerminal.selectAll()")
        self.pane.copy_button.click()
        self.wait(lambda: "中文命令和输出" in self.app.clipboard().text())
        self.key(Qt.Key_Insert, Qt.ControlModifier)
        self.assertIn("中文命令和输出", self.app.clipboard().text())
        # Clicking the terminal removes the selection; Ctrl+C must reach the PTY.
        QTest.mouseClick(self.pane.view.focusProxy(), Qt.LeftButton, pos=QPoint(300, 100))
        self.wait(lambda: not self.pane.copy_button.isEnabled())
        self.key(Qt.Key_C, Qt.ControlModifier)
        self.assertEqual(self.terminal.inputs, ["\x03"])
        self.app.clipboard().setText("preserved")
        self.key(Qt.Key_C, Qt.ControlModifier | Qt.ShiftModifier)
        self.assertEqual(self.app.clipboard().text(), "preserved")
        self.assertEqual(self.terminal.inputs, ["\x03"])

    def test_paste_shortcuts_button_and_bracketed_multiline(self):
        for key, modifiers in ((Qt.Key_V, Qt.ControlModifier),
                               (Qt.Key_V, Qt.ControlModifier | Qt.ShiftModifier),
                               (Qt.Key_Insert, Qt.ShiftModifier)):
            self.terminal.inputs.clear()
            self.app.clipboard().setText("echo 中文")
            self.key(key, modifiers)
            self.assertEqual(self.terminal.inputs, ["echo 中文"])
        self.terminal.inputs.clear()
        self.js('window.coworkTerminal.write("\\x1b[?2004h", 2)')
        self.wait(lambda: self.pane.cursor == 2)
        self.app.clipboard().setText("first\r\n第二行\nlast")
        self.pane.paste_button.click()
        self.wait(lambda: bool(self.terminal.inputs))
        self.assertEqual(self.terminal.inputs, ["\x1b[200~first\r第二行\rlast\x1b[201~"])

    def test_context_menu_and_control_lock(self):
        self.js("window.coworkTerminal.selectAll()")
        self.wait(lambda: self.pane.copy_button.isEnabled())
        QTest.mouseClick(self.pane.view.focusProxy(), Qt.RightButton, pos=QPoint(30, 15))
        self.wait(lambda: any(m.isVisible() for m in self.pane.findChildren(QMenu)))
        menu = next(m for m in self.pane.findChildren(QMenu) if m.isVisible())
        self.assertTrue(menu.actions()[0].isEnabled())
        menu.actions()[0].trigger()
        self.assertIn("中文命令和输出", self.app.clipboard().text())
        menu.close()
        self.store.set_owner(self.tab.session_id, self.tab.id, "agent")
        self.pane.refresh_control()
        self.js("true")
        self.assertFalse(self.pane.paste_button.isEnabled())
        self.terminal.inputs.clear()
        self.app.clipboard().setText("blocked")
        self.key(Qt.Key_V, Qt.ControlModifier)
        self.pane.terminal_bridge.paste()
        self.js('window.coworkTerminal.paste("blocked")')
        self.assertEqual(self.terminal.inputs, [])
        self.pane.copy_button.click()
        self.wait(lambda: "中文命令和输出" in self.app.clipboard().text())
        self.store.set_owner(self.tab.session_id, self.tab.id, "human")
        self.pane.refresh_control()
        self.js("true")
        self.terminal.status = "exited"
        self.pane.paste_button.click()
        self.wait(lambda: "终端未运行" in self.pane.notice.text())
        self.assertEqual(self.terminal.inputs, [])

    def test_capture(self):
        directory = os.environ.get("COWORK_TERMINAL_SCREENSHOTS")
        if not directory: return
        self.js("window.coworkTerminal.selectAll()")
        self.wait(lambda: self.pane.copy_button.isEnabled())
        QTest.qWait(200)
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        scale = os.environ.get("QT_SCALE_FACTOR", "1")
        self.assertTrue(self.pane.grab().save(str(path / f"terminal-clipboard-{scale}.png")))

    @unittest.skipUnless(os.name == "nt", "ConPTY requires Windows")
    def test_real_shell_command_and_output(self):
        from core.workbench_terminal import TerminalSession, terminal_profiles
        for profile in terminal_profiles():
            with self.subTest(shell=profile["id"]):
                tab = self.store.open("clipboard-test", "terminal", cwd=str(ROOT), shell=profile["id"])
                terminal = TerminalSession(tab.session_id, tab.id, profile, str(ROOT))
                previous = self.pane
                self.pane = TerminalPane(tab, self.store, WorkbenchBridge(), terminal)
                self.pane.resize(650, 340)
                self.pane.show()
                try:
                    self.wait(lambda: self.pane.ready)
                    terminal.start()
                    self.wait(lambda: terminal.status != "starting", timeout=30)
                    self.assertEqual(terminal.status, "running", terminal.error)
                    self.wait(lambda: "$ " in terminal.read()["output"] if profile["id"] == "bash"
                              else "PS " in terminal.read()["output"], timeout=20)
                    self.app.clipboard().setText(
                        "printf 'CLIPBOARD_%s\\n' 'OK_中文'" if profile["id"] == "bash"
                        else "Write-Output ('CLIPBOARD_' + 'OK_中文')")
                    self.key(Qt.Key_V, Qt.ControlModifier)
                    self.assertNotIn("CLIPBOARD_OK_中文", terminal.read()["output"])
                    self.key(Qt.Key_Return, Qt.NoModifier)
                    self.wait(lambda: "CLIPBOARD_OK_中文" in terminal.read()["output"])
                    self.wait(lambda: self.pane.cursor == terminal.read()["cursor"])
                    self.js("window.coworkTerminal.selectAll()")
                    self.key(Qt.Key_C, Qt.ControlModifier)
                    self.assertIn("CLIPBOARD_OK_中文", self.app.clipboard().text())
                    print(f"{profile['name']}: native paste, execute, copy Chinese output PASS", flush=True)
                except Exception:
                    print(f"{profile['name']}: {terminal.read()!r}", flush=True)
                    raise
                finally:
                    self.pane.close_pane()
                    terminal.thread.join(5)
                    self.assertFalse(terminal.thread.is_alive())
                    self.pane.close()
                    self.pane.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                    self.pane = previous


if __name__ == "__main__":
    unittest.main()
