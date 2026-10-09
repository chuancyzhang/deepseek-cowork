import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

file_spec = importlib.util.spec_from_file_location(
    "file_impl",
    os.path.join(os.path.dirname(__file__), "../skills/file-system/impl.py"),
)
file_impl = importlib.util.module_from_spec(file_spec)
file_spec.loader.exec_module(file_impl)

doc_spec = importlib.util.spec_from_file_location(
    "document_impl",
    os.path.join(os.path.dirname(__file__), "../ai_skills/document-reader/impl.py"),
)
document_impl = importlib.util.module_from_spec(doc_spec)
doc_spec.loader.exec_module(document_impl)


class TestOfficeSkill(unittest.TestCase):
    def setUp(self):
        self.workspace_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.workspace_dir, ignore_errors=True)

    def test_plain_text_tool_refuses_structured_documents(self):
        result = file_impl.text_file_read(self.workspace_dir, "test.docx")
        self.assertEqual(result["error"]["code"], "structured_document_not_supported")

        result = file_impl.apply_patch(
            self.workspace_dir,
            "*** Begin Patch\n*** Add File: test.pdf\n+content\n*** End Patch",
        )
        self.assertEqual(result["error"]["code"], "structured_document_not_supported")

    def test_document_read_handles_docx_xlsx_and_plain_text_stays_separate(self):
        from docx import Document
        import openpyxl

        doc = Document()
        doc.add_paragraph("Hello World")
        doc.save(os.path.join(self.workspace_dir, "test.docx"))

        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(["Name", "Age"])
        sheet.append(["Alice", 30])
        workbook.save(os.path.join(self.workspace_dir, "test.xlsx"))

        with open(os.path.join(self.workspace_dir, "test.txt"), "w", encoding="utf-8") as handle:
            handle.write("Just some text")

        docx_content = document_impl.document_read(self.workspace_dir, "test.docx")
        self.assertIn("Hello World", docx_content["content"])

        xlsx_content = document_impl.document_read(self.workspace_dir, "test.xlsx")
        self.assertIn("Alice", xlsx_content["content"])

        text_content = file_impl.text_file_read(self.workspace_dir, "test.txt")
        self.assertEqual(text_content["content"], "Just some text")

    def test_document_reader_is_default_on_without_runtime_dependency_install(self):
        manifest_path = os.path.join(
            os.path.dirname(__file__),
            "../ai_skills/document-reader/skill.json",
        )
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        with open(document_impl.__file__, "r", encoding="utf-8") as handle:
            implementation = handle.read()

        self.assertTrue(manifest["default_enabled"])
        self.assertEqual(manifest["python_dependencies"], [])
        self.assertNotIn("ensure_package_installed", implementation)

    def _read_batches(self, filename, **selectors):
        chunks, cursors, context = [], set(), {}
        for _ in range(500):
            result = document_impl.document_read(self.workspace_dir, filename, max_chars=2000,
                                                 _context=context, **selectors)
            self.assertTrue(result["ok"], result)
            self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), 2000)
            self.assertEqual(result["content"], "".join(section["text"] for section in result["sections"]))
            chunks.append(result["content"])
            if not result["has_more"]:
                self.assertIsNone(result["next_cursor"])
                self.assertNotIn("file_state", context)  # A batch is never a full-file read.
                return "".join(chunks)
            self.assertTrue(result["content"])
            self.assertNotIn(result["next_cursor"], cursors)
            cursors.add(result["next_cursor"])
            selectors = {"cursor": result["next_cursor"]}
        self.fail("续读未能结束")

    def test_large_excel_batches_reconstruct_selected_content_for_xlsx_and_xls(self):
        import openpyxl

        long_cell = '续读\t"内容"\\' * 1200
        expected = f"Sheet 数据:\nName\tValue\n长单元格\t{long_cell}\ntail\tdone\nSheet 第二表:\n最后一项\n"
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = "数据"
        for row in (("Name", "Value"), ("长单元格", long_cell), ("tail", "done")):
            sheet.append(row)
        book.create_sheet("第二表").append(["最后一项"])
        book.save(os.path.join(self.workspace_dir, "large.xlsx"))
        book.close()
        shutil.copyfile(os.path.join(os.path.dirname(__file__), "fixtures/document-reader-batches.xls"),
                        os.path.join(self.workspace_dir, "large.xls"))
        for ext in ("xlsx", "xls"):
            with self.subTest(format=ext):
                self.assertEqual(self._read_batches(f"large.{ext}"), expected)
                self.assertEqual(self._read_batches(f"large.{ext}", sheet_name="数据", cell_range="B2:B3"),
                                 f"Sheet 数据:\n{long_cell}\ndone\n")

    def test_long_paragraph_continuation_and_changed_file(self):
        from docx import Document

        text = '长段落"\\\t' * 1400
        document = Document()
        document.add_paragraph(text)
        document.add_paragraph("尾段")
        path = os.path.join(self.workspace_dir, "long.docx")
        document.save(path)
        self.assertEqual(self._read_batches("long.docx"), text + "\n尾段\n")
        batch = document_impl.document_read(self.workspace_dir, "long.docx", max_chars=2000)
        document.add_paragraph("文件已改")
        document.save(path)
        result = document_impl.document_read(self.workspace_dir, "long.docx", cursor=batch["next_cursor"])
        self.assertEqual(result["error"]["code"], "file_changed")

    def test_explicit_disable_still_overrides_document_reader_default(self):
        from core.config_manager import ConfigManager

        config = object.__new__(ConfigManager)
        config.config = {
            "disabled_skills": ["document-reader"],
            "enabled_skills": [],
        }
        self.assertFalse(config.is_skill_enabled("document-reader", default_enabled=True))

    def test_document_read_handles_pptx_and_pdf(self):
        from pptx import Presentation
        from pypdf import PdfWriter

        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[1])
        slide.shapes.title.text = "Quarterly Review"
        presentation.save(os.path.join(self.workspace_dir, "test.pptx"))

        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        with open(os.path.join(self.workspace_dir, "test.pdf"), "wb") as handle:
            writer.write(handle)

        pptx_content = document_impl.document_read(self.workspace_dir, "test.pptx")
        self.assertIn("Quarterly Review", pptx_content["content"])
        self.assertEqual(pptx_content["metadata"]["slide_count"], 1)

        pdf_content = document_impl.document_read(self.workspace_dir, "test.pdf")
        self.assertEqual(pdf_content["format"], "pdf")
        self.assertEqual(pdf_content["metadata"]["total_pages"], 1)


if __name__ == "__main__":
    unittest.main()
