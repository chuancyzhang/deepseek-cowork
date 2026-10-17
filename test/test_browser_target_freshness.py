"""Actual WebEngine regression: a ticking clock must not invalidate a button."""
import json
import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineCore import QWebEngineProfile
from core.workbench import WorkbenchBridge, WorkbenchStore, WorkbenchRequest
from ui.browser_panes import BrowserPane


HTML = """<!doctype html><meta charset="utf-8"><title>Live clock</title>
<style>body{background:white;color:black}#clock{display:block;width:200px;height:30px}
button{width:160px;height:40px}#overlay{display:none;position:fixed;inset:0;z-index:100;background:white}</style>
<span id="clock">0</span><button id="details" onclick="window.clicks++;document.querySelector('output').textContent='Opened'">Details</button>
<input aria-label="Name"><output></output><div id="overlay"></div><div style="height:2000px"></div>
<script>window.clicks=0;window.ticks=0;setInterval(()=>{document.querySelector('#clock').textContent=String(++window.ticks)},20)</script>
"""


class BrowserTargetFreshnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.store, self.bridge = WorkbenchStore(), WorkbenchBridge()
        self.profile = QWebEngineProfile()
        self.tab = self.store.open("a", "web", url="about:blank")
        self.pane = BrowserPane(self.tab, self.store, self.bridge, self.profile)
        self.pane.resize(840, 660)
        self.pane.show()
        self.wait(lambda: self.pane.document_ready)
        self.pane.document_ready = False
        self.pane.view.setHtml(HTML)
        self.wait(lambda: self.pane.document_ready)
        self.store.set_owner("a", self.tab.id, "agent")
        self.pane.refresh_control()
        # Chromium's first compositor frame can lag loadFinished in offscreen Qt.
        self.wait(lambda: self.js("window.ticks") >= 10)

    def tearDown(self):
        self.pane.close_pane()
        self.pane.close()
        self.pane.deleteLater()
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
            time.sleep(.01)
        self.fail("WebEngine condition timed out")

    def js(self, source, world=0):
        result = []
        self.pane.page.runJavaScript(source, world, result.append)
        self.wait(lambda: bool(result))
        return result[0]

    def request(self, operation, **arguments):
        return WorkbenchRequest("a", "r", operation, arguments, {}, time.monotonic() + 8,
                                revision=self.tab.control_revision)

    def observe(self):
        request = self.request("observe")
        self.pane.observe(request, request.finish)
        self.wait(request.event.is_set)
        self.assertTrue(request.result["ok"], request.result)
        return request.result

    def act(self, observation, action="click", **kwargs):
        button = next(el for el in observation["elements"] if el["name"] == "Details")
        args = dict(page_version=observation["page_version"], action=action, element_id=button["id"])
        args.update(kwargs)
        request = self.request("browser_action", **args)
        try:
            self.pane.act(request, request.finish)
        except Exception as exc:
            request.fail(exc)
        self.wait(request.event.is_set)
        return request.result

    def coordinates(self):
        return json.loads(self.js("JSON.stringify((()=>{const r=document.querySelector('#details').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})())"))

    def tick_after(self):
        before = self.js("window.ticks")
        self.wait(lambda: self.js("window.ticks") >= before + 3)

    def test_native_element_click_survives_live_clock_and_is_not_replayed(self):
        observation = self.observe()
        self.tick_after()
        result = self.act(observation)
        self.assertTrue(result["ok"], result)
        self.wait(lambda: self.js("window.clicks") == 1)
        self.assertEqual(self.act(observation)["status"], "stale_page")
        self.assertEqual(self.js("window.clicks"), 1)

    def test_coordinate_click_survives_live_clock(self):
        observation = self.observe()
        coordinates = self.coordinates()
        self.tick_after()
        result = self.act(observation, action="click_at", **coordinates)
        self.assertTrue(result["ok"], result)
        self.wait(lambda: self.js("window.clicks") == 1)

    def test_target_replacement_label_change_and_cover_are_rejected(self):
        for change in (
            "b.replaceWith(b.cloneNode(true))",
            "b.textContent='Delete'",
            "b.disabled=true",
            "document.querySelector('#overlay').style.display='block'",
        ):
            with self.subTest(change=change):
                self.js("document.querySelector('#details').outerHTML='<button id=details onclick=window.clicks++>Details</button>';document.querySelector('#overlay').style.display='none'")
                observation = self.observe()
                self.js("{const b=document.querySelector('#details');" + change + "}")
                result = self.act(observation)
                self.assertFalse(result["ok"], result)
                self.assertFalse(result["executed"], result)
                self.assertEqual(self.js("window.clicks"), 0)

    def test_coordinate_layout_change_and_guessed_version_are_rejected(self):
        observation = self.observe()
        coordinates = self.coordinates()
        self.js("document.querySelector('#details').style.marginLeft='150px'")
        self.assertFalse(self.act(observation, action="click_at", **coordinates)["ok"])
        observation = self.observe()
        observation["page_version"] += "0"
        self.assertEqual(self.act(observation)["status"], "stale_page")
        self.assertEqual(self.js("window.clicks"), 0)

    def test_external_accessible_label_change_invalidates_target(self):
        self.js("document.querySelector('output').id='label';document.querySelector('output').textContent='Details';document.querySelector('#details').setAttribute('aria-labelledby','label')")
        observation = self.observe()
        self.js("document.querySelector('#label').textContent='Delete everything'")
        self.assertEqual(self.act(observation)["status"], "stale_page")
        self.assertEqual(self.js("window.clicks"), 0)

    def test_coordinate_rejects_css_only_stacking_change(self):
        self.js("document.querySelector('#overlay').style.cssText='display:block;z-index:-1'")
        observation = self.observe()
        coordinates = self.coordinates()
        # CSSOM updates do not produce DOM mutation records.
        self.js("document.styleSheets[0].insertRule('#overlay{z-index:999!important}',0)")
        self.assertFalse(self.act(observation, action="click_at", **coordinates)["ok"])
        self.assertEqual(self.js("window.clicks"), 0)

    def test_fill_and_scroll_survive_clock_but_consume_observation(self):
        observation = self.observe()
        field = next(el for el in observation["elements"] if el["name"] == "Name")
        self.tick_after()
        result = self.act(observation, action="fill", element_id=field["id"], text="中文")
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.js("document.querySelector('input').value"), "中文")
        self.assertEqual(self.act(observation)["status"], "stale_page")
        observation = self.observe()
        self.tick_after()
        self.assertTrue(self.act(observation, action="scroll", y=300)["ok"])
        self.wait(lambda: self.js("scrollY") > 0)

    def test_takeover_between_validation_and_dispatch_cancels_click(self):
        observation = self.observe()
        button = next(el for el in observation["elements"] if el["name"] == "Details")
        request = self.request("browser_action", page_version=observation["page_version"], action="click", element_id=button["id"])
        self.pane.act(request, request.finish)
        self.pane.take_over()
        self.wait(request.event.is_set)
        self.assertFalse(request.result["ok"])
        self.assertFalse(request.result["executed"])
        self.assertEqual(self.js("window.clicks"), 0)
