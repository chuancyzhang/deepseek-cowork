import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from reportlab.pdfgen import canvas

from core import document_preview
from core.execution_authorization import ExecutionAuthorization, trusted_kind
from core.skill_manager import SkillManager
from core.tool_images import resolve_tool_image_path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("documents_render_test", ROOT / "skills/documents/impl.py")
documents = importlib.util.module_from_spec(spec)
spec.loader.exec_module(documents)


class DocumentRenderTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".tmp/document-render-tests"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.source = self.workspace / "source.pdf"
        pdf = canvas.Canvas(str(self.source))
        for index in range(5):
            pdf.drawString(60, 720, f"Page {index + 1}")
            pdf.showPage()
        pdf.save()
        self.original = self.source.read_bytes()
        self.context = {"session_id": "a"}
        self.runtime = patch("core.sandbox_runtime.get_runtime_executable", return_value=sys.executable)
        self.runtime.start()
        self.addCleanup(self.runtime.stop)
        self.cache = patch("core.document_preview.cache_root", return_value=self.workspace / "cache")
        self.cache.start()
        self.addCleanup(self.cache.stop)

    def render(self, **kwargs):
        return documents.document_render(str(self.workspace), str(self.source), _context=self.context, **kwargs)

    def test_real_pdf_batch_is_versioned_bounded_and_viewable(self):
        result = self.render()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["page_count"], 5)
        self.assertEqual(len(result["pages"]), 4)
        self.assertEqual(result["next_pages"], "5-5")
        self.assertEqual(result["source_version"], hashlib.sha256(self.original).hexdigest())
        for page in result["pages"]:
            path, _ = resolve_tool_image_path(str(self.workspace), page["path"], {})
            with Image.open(path) as picture:
                self.assertEqual(max(picture.size), 2000)
                self.assertGreater(picture.convert("L").getextrema()[1], picture.convert("L").getextrema()[0])
        last = self.render(pages=result["next_pages"], source_version=result["source_version"])
        self.assertTrue(last["ok"], last)
        self.assertEqual(last["pages"][0]["page"], 5)
        self.assertFalse(last["has_more"])
        self.assertEqual(self.source.read_bytes(), self.original)
        self.context["session_id"] = "b"
        other = self.render(pages="1")
        self.assertNotEqual(Path(other["pages"][0]["path"]).parts[2], Path(result["pages"][0]["path"]).parts[2])

    def test_invalid_ranges_changed_source_and_missing_renderer_preserve_source(self):
        for pages in ("0", "1-5", "3-2", "1;quit", "99"):
            with self.subTest(pages=pages):
                self.assertFalse(self.render(pages=pages)["ok"])
        self.assertEqual(self.render(source_version="old")["error"]["code"], "source_changed")
        source = self.workspace / "test.docx"
        source.write_bytes(b"original")
        with patch.object(document_preview, "render_office_pdf", side_effect=document_preview.WorkbenchError("renderer_unavailable", "missing")):
            result = documents.document_render(str(self.workspace), str(source), _context=self.context)
        self.assertEqual(result["error"]["code"], "renderer_unavailable")
        self.assertEqual(source.read_bytes(), b"original")
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertEqual(list((self.workspace / ".cowork/document-previews").glob("*/*")), [])

    def test_noncontiguous_details_and_overview_cover_the_whole_document(self):
        result=self.render(pages='1,5')
        self.assertTrue(result['ok'],result)
        self.assertEqual([page['page'] for page in result['pages']],[1,5])
        overview=self.render(review_kind='overview')
        self.assertTrue(overview['ok'],overview)
        self.assertEqual(overview['pages'][0]['page_numbers'],[1,2,3,4,5])
        self.assertEqual(overview['pages'][0]['width'],2000)
        self.assertEqual(self.source.read_bytes(),self.original)

    def test_font_diagnostics_report_unembedded_and_missing_declared_fonts(self):
        from docx import Document
        source=self.workspace/'font.docx'
        document=Document();document.add_paragraph().add_run('font check').font.name='CoworkMissingFontFixture';document.save(source)
        with patch('core.system_fonts.installed_font_families',return_value=['Arial']):
            warnings=document_preview.preview_diagnostics(source,self.source)
        self.assertTrue(any('未嵌入' in warning for warning in warnings))
        self.assertTrue(any('CoworkMissingFontFixture' in warning for warning in warnings))

    def test_worker_cancellation_timeout_and_source_change_are_errors(self):
        out = self.workspace / "out"
        out.mkdir()
        stop = threading.Event()
        process = Mock(returncode=0)
        process.poll.return_value = None
        process.communicate.return_value = (b"", b"")
        def start(*args, **kwargs):
            stop.set()
            return process
        with patch("core.process_utils.popen_external_program", side_effect=start), patch("core.process_utils.terminate_process_tree") as terminate:
            with self.assertRaises(document_preview.WorkbenchError) as caught:
                document_preview.render_document_pages(self.source, out, cancel=stop)
            self.assertEqual(caught.exception.code, "cancelled")
            terminate.assert_called_once_with(process)
        with patch("core.process_utils.popen_external_program", return_value=process), patch("core.process_utils.terminate_process_tree"):
            with self.assertRaises(document_preview.WorkbenchError) as caught:
                document_preview.render_document_pages(self.source, out, timeout=-1)
            self.assertEqual(caught.exception.code, "renderer_timeout")
        process.poll.return_value = 0
        def changed(*args, **kwargs):
            self.source.write_bytes(self.original + b"changed")
            return json.dumps({"pages": [], "page_count": 5}).encode(), b""
        process.communicate.side_effect = changed
        with patch("core.process_utils.popen_external_program", return_value=process):
            with self.assertRaises(document_preview.WorkbenchError) as caught:
                document_preview.render_document_pages(self.source, out)
            self.assertEqual(caught.exception.code, "source_changed")

    def test_stopped_run_does_not_start_worker_and_exact_handler_is_trusted(self):
        auth = ExecutionAuthorization("a", "run", False, str(self.workspace))
        auth.cancel()
        self.context["execution_authorization"] = auth
        with patch.object(document_preview, "render_document_pages") as render:
            result = self.render()
        self.assertFalse(result["ok"])
        render.assert_not_called()
        self.assertEqual(trusted_kind("document_render", documents.document_render), "read")
        def document_render():
            pass
        self.assertEqual(trusted_kind("document_render", document_render), "opaque")

    def test_cleanup_failure_preserves_render_failure_and_reports_retained_cache(self):
        with patch.object(document_preview, "render_document_pages", side_effect=document_preview.WorkbenchError("renderer_failed", "bad PDF")), patch.object(documents.shutil, "rmtree", side_effect=OSError("busy")):
            result = self.render()
        self.assertEqual(result["error"]["code"], "renderer_failed")
        self.assertIn("bad PDF", result["error"]["message"])
        self.assertIn("缓存", result["error"]["message"])
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_old_skill_is_retired_without_alias(self):
        manager = SkillManager(workspace_dir=str(self.workspace), auto_load=False)
        self.assertFalse(manager._is_skill_enabled_for_path("document-reader", str(ROOT / "skills/documents")))
        self.assertIsNone(manager._find_skill_path("document-reader"))
        self.assertFalse((ROOT / "skills/document-reader").exists())
