import copy
import io
from pathlib import Path
import tempfile
import unittest
import zipfile
from lxml import etree

from core.docx_review import WordReviewDocument, save_review, tag, NS
from core.deliverable_editing import (create_edit_session, load_editor_payload, serialize_editor_payload,
                                     atomic_save_session, preflight_edit, backup_paths, ExternalModificationError)
from docx_review_fixture import make_review_docx


class WordReviewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "review.docx"
        self.parts = make_review_docx(self.path)
        self.source = self.path.read_bytes()
        self.model = WordReviewDocument(self.source)
        self.payload = {"version": 1, "regions": dict(self.model.snapshot["regions"]), "revisions": {}}

    def edit(self, before, after):
        key = next(key for key, text in self.payload["regions"].items() if before in text)
        self.payload["regions"][key] = self.payload["regions"][key].replace(before, after)

    def parts_after(self):
        with zipfile.ZipFile(io.BytesIO(save_review(self.source, self.payload))) as archive:
            return {name: archive.read(name) for name in archive.namelist()}

    def replace_xml(self, part, root):
        self.parts[part] = etree.tostring(root, encoding="UTF-8", xml_declaration=True)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in self.parts.items():
                archive.writestr(name, content)
        self.source = buffer.getvalue()
        self.path.write_bytes(self.source)
        self.model = WordReviewDocument(self.source)
        self.payload = {"version": 1, "regions": dict(self.model.snapshot["regions"]), "revisions": {}}

    def test_preflight_opens_review_and_unchanged_save_is_byte_identical(self):
        session, report = create_edit_session(str(self.path))
        self.assertTrue(report.allowed)
        self.assertTrue(session.metadata["docx_review"])
        loaded = load_editor_payload(session)
        self.assertEqual(loaded["kind"], "docx_review")
        self.assertEqual([section["title"] for section in loaded["snapshot"]["sections"]], ["正文", "脚注 1", "尾注 1"])
        self.assertEqual(serialize_editor_payload(session, self.payload), self.source)

    def test_body_notes_table_edits_preserve_all_other_parts_and_revision_records(self):
        for before, after in (("正文原文", "正文更新"), ("脚注原文", "脚注更新"), ("尾注原文", "尾注更新"), ("表格原文", "表格更新")):
            self.edit(before, after)
        result = self.parts_after()
        for part in self.parts:
            if part not in {"word/document.xml", "word/footnotes.xml", "word/endnotes.xml"}:
                self.assertEqual(result[part], self.parts[part], part)
        for part in ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml"):
            old, new = etree.fromstring(self.parts[part]), etree.fromstring(result[part])
            for kind in ("ins", "del", "pPrChange", "footnoteReference", "endnoteReference"):
                self.assertEqual([etree.tostring(n) for n in old.iter(tag(kind))], [etree.tostring(n) for n in new.iter(tag(kind))])
        self.assertIn("正文更新", result["word/document.xml"].decode())
        self.assertIn("脚注更新", result["word/footnotes.xml"].decode())
        self.assertIn("尾注更新", result["word/endnotes.xml"].decode())

    def test_accept_and_reject_insertions_deletions_in_body_and_notes(self):
        for decision in ("accept", "reject"):
            with self.subTest(decision=decision):
                self.payload["revisions"] = {key: decision for key in self.model.revisions}
                result = self.parts_after()
                body = etree.fromstring(result["word/document.xml"])
                text = "".join(body.itertext())
                self.assertEqual("新增文字" in text, decision == "accept")
                self.assertEqual("删除文字" in text, decision == "reject")
                self.assertFalse(list(body.iter(tag("ins"))))
                self.assertFalse(list(body.iter(tag("del"))))
                self.assertFalse(list(body.iter(tag("delText"))))
                notes = etree.fromstring(result["word/footnotes.xml"])
                self.assertEqual("脚注新增文字" in "".join(notes.itertext()), decision == "accept")
                paragraph = next(p for p in body.iter(tag("p")) if "格式修订段落" in "".join(p.itertext()))
                self.assertEqual(paragraph.find("w:pPr/w:jc", NS).get(tag("val")), "right" if decision == "accept" else "center")

    def test_review_choice_and_later_text_edit_do_not_resurrect_changes_on_second_save(self):
        self.edit("新增文字", "接受后修改的文字")
        self.payload["revisions"] = {key: "accept" for key, node in self.model.revisions.items() if node.get(tag("id")) == "10"}
        session, _ = create_edit_session(str(self.path))
        load_editor_payload(session)
        backup = str(Path(self.temp.name) / "backups")
        first = serialize_editor_payload(session, self.payload)
        atomic_save_session(session, first, backup_root=backup)
        self.edit("正文原文", "再次保存的正文")
        second = serialize_editor_payload(session, self.payload)
        atomic_save_session(session, second, backup_root=backup)
        self.assertEqual(Path(backup_paths(str(self.path), backup)[0]).read_bytes(), first)
        self.assertEqual(self.path.read_bytes(), second)
        self.assertTrue(preflight_edit(str(self.path)).allowed)
        with zipfile.ZipFile(self.path) as archive:
            text = archive.read("word/document.xml").decode()
            self.assertIn("再次保存的正文", text)
            self.assertIn("接受后修改的文字", text)
            self.assertNotIn('w:ins w:id="10"', text)

    def test_text_insertion_deletion_and_newlines_keep_run_formatting(self):
        self.edit("保留粗体和普通文字。", "新保留粗体和新的普通文字。\n第二行\t内容")
        body = etree.fromstring(self.parts_after()["word/document.xml"])
        paragraph = next(p for p in body.iter(tag("p")) if "保留粗体" in "".join(p.itertext()))
        bold = paragraph.findall("w:r", NS)[0]
        self.assertIsNotNone(bold.find("w:rPr/w:b", NS))
        self.assertIn("保留粗体", "".join(bold.itertext()))
        self.assertEqual(len(list(paragraph.iter(tag("br")))), 1)
        self.assertEqual(len(list(paragraph.iter(tag("tab")))), 1)

    def test_empty_table_paragraph_can_be_edited(self):
        key = next(key for key, value in self.payload["regions"].items() if not value)
        self.payload["regions"][key] = "新增单元格文字"
        self.assertIn("新增单元格文字", self.parts_after()["word/document.xml"].decode())

    def test_unknown_ids_and_invalid_choices_are_rejected_without_file_writes(self):
        for mutation in (lambda p: p["regions"].update(unknown="x"),
                         lambda p: p["regions"].pop(next(iter(p["regions"]))),
                         lambda p: p["revisions"].update(unknown="accept"),
                         lambda p: p["revisions"].update({next(iter(self.model.revisions)): "discard"}),
                         lambda p: p["regions"].update({next(iter(p["regions"])): "\x00"})):
            payload = copy.deepcopy(self.payload)
            mutation(payload)
            with self.assertRaises(ValueError):
                save_review(self.source, payload)
            self.assertEqual(self.path.read_bytes(), self.source)

    def test_external_write_is_not_overwritten_by_review_save(self):
        session, _ = create_edit_session(str(self.path))
        load_editor_payload(session)
        self.edit("正文原文", "本地草稿")
        candidate = serialize_editor_payload(session, self.payload)
        self.path.write_bytes(b"external update")
        with self.assertRaises(ExternalModificationError):
            atomic_save_session(session, candidate, backup_root=str(Path(self.temp.name) / "backups"))
        self.assertEqual(self.path.read_bytes(), b"external update")

    def test_paragraph_mark_deletion_merges_text_and_keeps_surviving_properties(self):
        root = etree.fromstring(self.parts["word/document.xml"])
        body = root.find(tag("body"))
        first, second = body.findall(tag("p"))[3:5]
        properties = etree.SubElement(first, tag("pPr"))
        run_properties = etree.SubElement(properties, tag("rPr"))
        etree.SubElement(run_properties, tag("del"), {tag("id"): "99"})
        self.replace_xml("word/document.xml", root)
        identifier = next(key for key, node in self.model.revisions.items() if node.get(tag("id")) == "99")
        self.payload["revisions"][identifier] = "accept"
        result = etree.fromstring(self.parts_after()["word/document.xml"])
        paragraph = next(p for p in result.iter(tag("p")) if "保留粗体" in "".join(p.itertext()))
        self.assertIn("格式修订段落", "".join(paragraph.itertext()))
        self.assertEqual(paragraph.find("w:pPr/w:jc", NS).get(tag("val")), "right")
        self.assertIsNotNone(paragraph.find("w:pPr/w:pPrChange", NS))

    def test_rejecting_inserted_table_rows_removes_empty_table(self):
        root = etree.fromstring(self.parts["word/document.xml"])
        for index, row in enumerate(root.iter(tag("tr"))):
            properties = etree.Element(tag("trPr")); row.insert(0, properties)
            etree.SubElement(properties, tag("ins"), {tag("id"): str(90 + index)})
        self.replace_xml("word/document.xml", root)
        self.payload["revisions"] = {key: "reject" for key, node in self.model.revisions.items() if node.getparent().tag == tag("trPr")}
        result = etree.fromstring(self.parts_after()["word/document.xml"])
        self.assertFalse(list(result.iter(tag("tbl"))))
        self.assertIn("脚注原文", self.parts_after()["word/footnotes.xml"].decode())

    def test_complex_revisions_are_visible_preserved_and_not_falsely_resolved(self):
        root = etree.fromstring(self.parts["word/document.xml"])
        change = next(root.iter(tag("ins")))
        change.tag = tag("moveTo")
        self.replace_xml("word/document.xml", root)
        item = next(item for item in self.model.snapshot["revisions"] if item["kind"] == "moveTo")
        self.assertFalse(item["supported"])
        self.edit("正文原文", "正文修改")
        self.assertIn("moveTo", self.parts_after()["word/document.xml"].decode())
        self.payload["revisions"][item["id"]] = "accept"
        with self.assertRaisesRegex(ValueError, "Word"):
            save_review(self.source, self.payload)
        self.assertEqual(self.path.read_bytes(), self.source)

    def test_pending_nested_deletion_keeps_deleted_text_after_outer_rejection(self):
        root = etree.fromstring(self.parts["word/document.xml"])
        outer = next(root.iter(tag("del")))
        nested = etree.SubElement(outer, tag("del"), {tag("id"): "98"})
        etree.SubElement(etree.SubElement(nested, tag("r")), tag("delText")).text = "嵌套删除"
        self.replace_xml("word/document.xml", root)
        self.payload["revisions"] = {key: "reject" for key, node in self.model.revisions.items() if node.get(tag("id")) == "11"}
        result = etree.fromstring(self.parts_after()["word/document.xml"])
        pending = next(result.iter(tag("del")))
        self.assertEqual(pending.find("w:r/w:delText", NS).text, "嵌套删除")

    def test_field_result_in_footnote_is_protected(self):
        root = etree.fromstring(self.parts["word/footnotes.xml"])
        paragraph = root.findall("w:footnote/w:p", NS)[-1]
        for kind in ("begin", "separate", "end"):
            etree.SubElement(etree.SubElement(paragraph, tag("r")), tag("fldChar"), {tag("fldCharType"): kind})
            if kind == "separate":
                etree.SubElement(etree.SubElement(paragraph, tag("r")), tag("t")).text = "FIELD RESULT"
        self.replace_xml("word/footnotes.xml", root)
        self.assertNotIn("FIELD RESULT", " ".join(self.model.snapshot["regions"].values()))
        self.edit("脚注原文", "修改脚注")
        self.assertIn("FIELD RESULT", self.parts_after()["word/footnotes.xml"].decode())


if __name__ == "__main__":
    unittest.main()
