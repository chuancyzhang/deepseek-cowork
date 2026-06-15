import importlib.util
import os
import shutil
import sys

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


def test_plain_text_tool_refuses_structured_documents():
    workspace_dir = os.path.abspath("test_workspace")
    if os.path.exists(workspace_dir):
        shutil.rmtree(workspace_dir)
    os.makedirs(workspace_dir)
    try:
        result = file_impl.text_file_read(workspace_dir, "test.docx")
        assert "structured_document_not_supported" in result

        result = file_impl.text_file_write(workspace_dir, "test.pdf", "content")
        assert "structured_document_not_supported" in result
    finally:
        shutil.rmtree(workspace_dir, ignore_errors=True)


def test_document_read_handles_docx_xlsx_and_plain_text_stays_separate():
    workspace_dir = os.path.abspath("test_workspace")
    if os.path.exists(workspace_dir):
        shutil.rmtree(workspace_dir)
    os.makedirs(workspace_dir)
    try:
        from docx import Document
        import openpyxl

        doc = Document()
        doc.add_paragraph("Hello World")
        doc.save(os.path.join(workspace_dir, "test.docx"))

        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(["Name", "Age"])
        sheet.append(["Alice", 30])
        workbook.save(os.path.join(workspace_dir, "test.xlsx"))

        with open(os.path.join(workspace_dir, "test.txt"), "w", encoding="utf-8") as handle:
            handle.write("Just some text")

        docx_content = document_impl.document_read(workspace_dir, "test.docx")
        assert "Hello World" in docx_content

        xlsx_content = document_impl.document_read(workspace_dir, "test.xlsx")
        assert "Alice" in xlsx_content

        text_content = file_impl.text_file_read(workspace_dir, "test.txt")
        assert "Just some text" in text_content
    finally:
        shutil.rmtree(workspace_dir, ignore_errors=True)
