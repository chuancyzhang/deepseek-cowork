"""Real WebEngine coverage for clipped pages, human zoom and native coordinates."""
import json
import os
from pathlib import Path
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStackedWidget
from PySide6.QtWebEngineCore import QWebEngineProfile
from core.workbench import WorkbenchBridge, WorkbenchStore, WorkbenchRequest, WorkbenchError
from ui.browser_panes import BrowserPane

HTML = (Path(__file__).parent / "fixtures/browser-wide-page.html").read_text(encoding="utf-8")


class BrowserZoomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.store, self.bridge = WorkbenchStore(), WorkbenchBridge()
        self.profile = QWebEngineProfile()
        self.tab = self.store.open("zoom", "web", url="about:blank")
        self.pane = BrowserPane(self.tab, self.store, self.bridge, self.profile)
        self.panes = [self.pane]
        self.pane.resize(840, 660)
        self.pane.show()
        self.wait(lambda: self.pane.document_ready)
        self.pane.document_ready = False
        self.pane.view.setHtml(HTML)
        self.wait(lambda: self.pane.document_ready)
        self.wait(lambda: self.js("innerWidth") == self.pane.view.width())

    def tearDown(self):
        for pane in self.panes:
            pane.close_pane()
            pane.close()
            pane.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.profile.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def wait(self, predicate, timeout=8):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            QTest.qWait(10)
        self.fail("WebEngine did not reach the expected state")

    def js(self, source):
        result = []
        self.pane.page.runJavaScript(source, result.append)
        self.wait(lambda: bool(result))
        return result[0]

    def observe(self):
        request = self.request("observe")
        self.pane.observe(request, request.finish)
        self.wait(request.event.is_set)
        self.assertTrue(request.result["ok"], request.result)
        return request.result

    def request(self, operation, **arguments):
        return WorkbenchRequest("zoom", "run", operation, arguments, {},
                                time.monotonic() + 8, revision=self.tab.control_revision)

    def wheel(self, delta):
        target = self.pane.view.focusProxy() or self.pane.view
        event = QWheelEvent(QPointF(30, 30), QPointF(target.mapToGlobal(QPoint(30, 30))),
                            QPoint(), QPoint(0, delta), Qt.NoButton, Qt.ControlModifier,
                            Qt.NoScrollPhase, False)
        QApplication.sendEvent(target, event)

    def test_clipped_page_becomes_visible_without_reloading_or_losing_input(self):
        self.assertLess(self.js("document.querySelector('main').getBoundingClientRect().left"), 0)
        self.js("document.querySelector('input').value='保留用户草稿';window.marker=42")
        for _ in range(5):
            self.pane.zoom_out.click()
        self.assertEqual(self.pane.view.zoomFactor(), .5)
        self.wait(lambda: self.js("document.querySelector('main').getBoundingClientRect().left") >= 0)
        self.assertTrue(self.js("document.querySelector('main').getBoundingClientRect().right <= innerWidth"))
        self.assertEqual(self.js("document.querySelector('input').value"), "保留用户草稿")
        self.assertEqual(self.js("window.marker"), 42)
        self.pane.zoom_reset.click()
        self.wait(lambda: self.js("innerWidth") == self.pane.view.width())
        self.assertLess(self.js("document.querySelector('main').getBoundingClientRect().left"), 0)

    def test_keyboard_and_wheel_work_with_web_content_focused_and_have_limits(self):
        self.pane.activateWindow()
        target = self.pane.view.focusProxy() or self.pane.view
        target.setFocus()
        QTest.keyClick(target, Qt.Key_Minus, Qt.ControlModifier)
        self.wait(lambda: self.pane.view.zoomFactor() == .9)
        QTest.keyClick(target, Qt.Key_Equal, Qt.ControlModifier)
        self.wait(lambda: self.pane.view.zoomFactor() == 1.)
        self.wheel(-120)
        self.assertEqual(self.pane.view.zoomFactor(), .9)
        self.wheel(120)
        self.assertEqual(self.pane.view.zoomFactor(), 1.)
        self.pane.set_zoom(.25)
        self.wheel(-120)
        self.assertEqual(self.pane.view.zoomFactor(), .25)
        self.assertFalse(self.pane.zoom_out.isEnabled())
        self.pane.set_zoom(5.)
        self.wheel(120)
        self.assertAlmostEqual(self.pane.view.zoomFactor(), 5.)
        self.assertFalse(self.pane.zoom_in.isEnabled())
        QTest.keyClick(target, Qt.Key_0, Qt.ControlModifier)
        self.wait(lambda: self.pane.view.zoomFactor() == 1.)

    def test_agent_lock_and_old_observation_then_native_click_at_new_zoom(self):
        before = self.observe()
        self.store.set_owner("zoom", self.tab.id, "agent")
        self.pane.refresh_control()
        self.pane.set_zoom(.5)
        self.pane.zoom_out.click()
        self.wheel(-120)
        self.pane.activateWindow()
        target = self.pane.view.focusProxy() or self.pane.view
        target.setFocus()
        QTest.keyClick(target, Qt.Key_Minus, Qt.ControlModifier)
        self.assertEqual(self.pane.view.zoomFactor(), 1.)
        self.assertFalse(self.pane.zoom_reset.isEnabled())
        self.pane.take_over()
        self.pane.set_zoom(.5)
        self.wait(lambda: self.js("innerWidth") > 1440)
        self.pane.give_back()
        self.store.set_owner("zoom", self.tab.id, "agent")
        self.pane.refresh_control()
        old = self.request("browser_action", action="click_at",
                           page_version=before["page_version"], x=10, y=10)
        with self.assertRaises(WorkbenchError) as error:
            self.pane.act(old, old.finish)
        self.assertEqual(error.exception.code, "stale_page")
        observation = self.observe()
        coordinates = json.loads(self.js("JSON.stringify((()=>{const r=document.querySelector('#save').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})())"))
        click = self.request("browser_action", action="click_at",
                             page_version=observation["page_version"], **coordinates)
        self.pane.act(click, click.finish)
        self.wait(click.event.is_set)
        self.assertTrue(click.result["ok"], click.result)
        self.wait(lambda: self.js("window.saves") == 1)

    def test_zoom_stays_with_tab_when_parked_and_other_chat_is_opened(self):
        self.pane.set_zoom(.5)
        stack = QStackedWidget()
        try:
            self.pane.park(self.pane.size())
            other_tab = self.store.open("other-chat", "web", url="about:blank")
            other = BrowserPane(other_tab, self.store, self.bridge, self.profile)
            self.panes.append(other)
            self.wait(lambda: other.document_ready)
            self.assertEqual(other.view.zoomFactor(), 1.)
            other.set_zoom(1.25)
            self.pane.mount(stack)
            self.assertEqual(self.pane.view.zoomFactor(), .5)
            self.assertEqual(self.pane.zoom_reset.text(), "50%")
            self.assertEqual(other.view.zoomFactor(), 1.25)
        finally:
            stack.removeWidget(self.pane)
            self.pane.setParent(None)
            stack.deleteLater()


if __name__ == "__main__":
    unittest.main()
