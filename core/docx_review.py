"""Edit Word text and review changes without rebuilding the OOXML package.

The browser receives an editable projection, never the authority to replace
arbitrary package parts. Text regions and revision IDs are bound to a retained
source snapshot; only those operations are written back to the original XML.
"""
from __future__ import annotations

import bisect
import copy
import difflib
import html
import io
import logging
import re
import zipfile

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
REVISION_TAGS = {"ins", "del", "moveFrom", "moveTo", "rPrChange", "pPrChange",
                 "tblPrChange", "trPrChange", "tcPrChange", "sectPrChange", "tblGridChange",
                 "cellIns", "cellDel", "cellMerge", "numberingChange"}
COMPLEX_REVISIONS = {"moveFrom", "moveTo", "cellIns", "cellDel", "cellMerge", "numberingChange"}
PROTECTED = {"drawing": "图片", "pict": "图片", "object": "嵌入对象",
             "sdt": "内容控件", "fldSimple": "域", "oMath": "公式", "oMathPara": "公式"}
log = logging.getLogger(__name__)


def tag(name):
    return f"{{{W}}}{name}"


def local(node):
    return etree.QName(node).localname if isinstance(node.tag, str) else ""


def word(node, name):
    return node.tag == tag(name)


def parse(data):
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("文档 XML 含不支持的实体声明。")
    try:
        return etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True))
    except etree.XMLSyntaxError as exc:
        raise ValueError("DOCX XML 结构无效，原文件未修改。") from exc


def needs_review(names, document_xml, archive):
    document = parse(document_xml)
    for kind in ("footnote", "endnote"):
        part = f"word/{kind}s.xml"
        if part in names:
            root = parse(archive.read(part))
            references = {node.get(tag("id")) for node in document.iter(tag(kind + "Reference"))}
            if any(word(node, kind) and node.get(tag("type"), "normal") == "normal"
                   and (node.get(tag("id")) in references or node.get(tag("id")) not in {"-1", "0"}) for node in root):
                return True
    return any(node.tag in {tag(name) for name in REVISION_TAGS} for node in document.iter())


class WordReviewDocument:
    def __init__(self, source: bytes):
        self.source = source
        with zipfile.ZipFile(io.BytesIO(source)) as archive:
            self.parts = {item.filename: archive.read(item) for item in archive.infolist()}
        self.roots = {}
        self.nodes = {}
        self.ids = {}
        self.regions = {}
        self.revisions = {}
        self.note_labels = {}
        self._field_depth = 0
        for part in ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml"):
            if part not in self.parts:
                continue
            root = parse(self.parts[part])
            self.roots[part] = root
            for index, node in enumerate(root.iter()):
                if not isinstance(node.tag, str):
                    continue
                identifier = f"{len(self.roots)}-{index}"
                self.nodes[identifier] = (part, node)
                self.ids[node] = identifier
                if node.tag in {tag(name) for name in REVISION_TAGS} and not any(
                    local(ancestor).endswith("Change") for ancestor in node.iterancestors()
                ):
                    self.revisions[identifier] = node
        self._validate_notes()
        self.snapshot = self._snapshot()

    def _validate_notes(self):
        main = self.roots["word/document.xml"]
        for kind in ("footnote", "endnote"):
            root = self.roots.get(f"word/{kind}s.xml")
            notes = {} if root is None else {node.get(tag("id")): node for node in root if word(node, kind)}
            refs = list(main.iter(tag(f"{kind}Reference")))
            for reference in refs:
                identifier = reference.get(tag("id"))
                if identifier not in notes:
                    raise ValueError("文档中的脚注或尾注引用没有对应内容，无法安全编辑。")
                key = (kind, identifier)
                if key not in self.note_labels:
                    self.note_labels[key] = sum(key[0] == kind for key in self.note_labels) + 1
            for identifier, node in notes.items():
                if node.get(tag("type"), "normal") == "normal" and identifier not in {"-1", "0"}:
                    self.note_labels.setdefault((kind, identifier), len([k for k in self.note_labels if k[0] == kind]) + 1)

    def _style(self, node):
        run = next((parent for parent in node.iterancestors() if word(parent, "r")), None)
        if run is None:
            return ""
        properties = run.find(tag("rPr"))
        if properties is None:
            return ""
        styles = []
        for name, css in (("b", "font-weight:bold"), ("i", "font-style:italic"),
                          ("strike", "text-decoration:line-through")):
            prop = properties.find(tag(name))
            if prop is not None and prop.get(tag("val"), "true") not in {"0", "false", "off"}:
                styles.append(css)
        underline = properties.find(tag("u"))
        if underline is not None and underline.get(tag("val")) != "none":
            styles.append("text-decoration:underline")
        return ";".join(styles)

    def _paragraph(self, paragraph):
        pieces, pending = [], []
        def flush():
            if not pending:
                return
            identifier = self.ids[pending[0]]
            self.regions[identifier] = (list(pending), paragraph)
            rendered = "".join(f'<span style="{self._style(node)}">{html.escape(node.text or "")}</span>' for node in pending)
            pieces.append(f'<span class="word-text" data-region="{identifier}" contenteditable="true" spellcheck="false">{rendered}</span>')
            pending.clear()

        def visit(node):
            name = local(node)
            if word(node, "pPr") or word(node, "rPr"):
                return
            if word(node, "fldChar"):
                flush()
                kind = node.get(tag("fldCharType"))
                if kind == "begin":
                    if not self._field_depth:
                        pieces.append('<span class="word-preserved">〔域 · 原样保留〕</span>')
                    self._field_depth += 1
                elif kind == "end":
                    self._field_depth = max(0, self._field_depth - 1)
            elif self._field_depth and node.tag in {tag("t"), tag("delText"), tag("instrText"), tag("delInstrText")}:
                return
            elif node.tag in {tag("t"), tag("delText")}:
                pending.append(node)
            elif node.tag in {tag("ins"), tag("del"), tag("moveFrom"), tag("moveTo")}:
                flush()
                identifier = self.ids[node]
                start = len(pieces)
                for child in node:
                    visit(child)
                flush()
                inner = "".join(pieces[start:])
                del pieces[start:]
                pieces.append(f'<span class="word-revision {name}" data-revision="{identifier}">{inner}</span>')
            elif word(node, "hyperlink"):
                flush()
                for child in node:
                    visit(child)
                flush()  # Do not move text into or out of the hyperlink relationship.
            elif name in {"footnoteReference", "endnoteReference"} and node.tag.startswith("{" + W + "}"):
                flush()
                kind = name.removesuffix("Reference")
                number = self.note_labels[(kind, node.get(tag("id")))]
                label = "脚注" if kind == "footnote" else "尾注"
                target = html.escape(f"{kind}-{node.get(tag('id'))}", quote=True)
                pieces.append(f'<button class="word-note-ref" data-note="{target}" title="跳转{label} {number}">{number}</button>')
            elif name in {"footnoteRef", "endnoteRef"}:
                pass  # The note heading already supplies its number.
            elif word(node, "br") or word(node, "cr") or word(node, "tab"):
                flush()
                pieces.append("<br>" if name != "tab" else '<span class="word-tab">\t</span>')
            elif name in PROTECTED:
                flush()
                pieces.append(f'<span class="word-preserved">〔{PROTECTED[name]} · 原样保留〕</span>')
            elif name in {"instrText", "delInstrText", "fldChar"}:
                flush()
                pieces.append('<span class="word-preserved">〔域 · 原样保留〕</span>')
            else:
                for child in node:
                    visit(child)
        for child in paragraph:
            visit(child)
        flush()
        if not pieces:
            identifier = self.ids[paragraph] + "-empty"
            self.regions[identifier] = ([], paragraph)
            pieces.append(f'<span class="word-text" data-region="{identifier}" contenteditable="true" spellcheck="false"></span>')
        style = ""
        align = paragraph.find("w:pPr/w:jc", NS)
        if align is not None and align.get(tag("val")) in {"left", "right", "center", "both"}:
            style = "text-align:" + {"both": "justify"}.get(align.get(tag("val")), align.get(tag("val")))
        marker = paragraph.find("w:pPr/w:rPr/w:del", NS)
        if marker is None:
            marker = paragraph.find("w:pPr/w:rPr/w:ins", NS)
        if marker is not None and marker in self.ids:
            pieces.append(f'<span class="word-paragraph-mark word-revision {local(marker)}" data-revision="{self.ids[marker]}">¶</span>')
        return f'<p class="word-paragraph" style="{style}">' + "".join(pieces) + "</p>"

    def _blocks(self, parent):
        result = []
        for node in parent:
            if word(node, "p"):
                result.append(self._paragraph(node))
            elif word(node, "tbl"):
                rows = []
                for row in node.findall("w:tr", NS):
                    cells = "".join("<td>" + self._blocks(cell) + "</td>" for cell in row.findall("w:tc", NS))
                    marker = row.find("w:trPr/w:ins", NS)
                    if marker is None:
                        marker = row.find("w:trPr/w:del", NS)
                    attributes = f' class="word-revision {local(marker)}" data-revision="{self.ids[marker]}"' if marker is not None else ""
                    rows.append(f"<tr{attributes}>" + cells + "</tr>")
                result.append('<div class="word-table-scroll"><table>' + "".join(rows) + "</table></div>")
            elif local(node) in {"ins", "del", "moveFrom", "moveTo"}:
                result.append(f'<div class="word-revision {local(node)}" data-revision="{self.ids[node]}">' + self._blocks(node) + "</div>")
        return "".join(result)

    def _snapshot(self):
        sections = [{"id": "body", "title": "正文", "html": self._blocks(self.roots["word/document.xml"].find(tag("body")))}]
        for kind, title in (("footnote", "脚注"), ("endnote", "尾注")):
            root = self.roots.get(f"word/{kind}s.xml")
            if root is None:
                continue
            for node in root:
                key = (kind, node.get(tag("id")))
                if key not in self.note_labels:
                    continue
                sections.append({"id": f"{kind}-{key[1]}", "title": f"{title} {self.note_labels[key]}", "html": self._blocks(node)})
        revisions = []
        labels = {"ins": "插入", "del": "删除", "moveFrom": "移出", "moveTo": "移入",
                  "cellIns": "插入单元格", "cellDel": "删除单元格", "cellMerge": "合并单元格", "numberingChange": "编号修改"}
        for identifier, node in self.revisions.items():
            name = local(node)
            revisions.append({"id": identifier, "kind": name, "label": labels.get(name, "格式修改"),
                              "author": node.get(tag("author"), ""), "date": node.get(tag("date"), ""),
                              "text": "".join(node.itertext())[:240],
                              "supported": name not in COMPLEX_REVISIONS,
                              "part": self.nodes[identifier][0]})
        return {"version": 1, "sections": sections, "revisions": revisions,
                "regions": {key: "".join(node.text or "" for node in nodes) for key, (nodes, _p) in self.regions.items()}}

    def save(self, payload):
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ValueError("Word 编辑数据版本无效，编辑内容仍保留，请重新保存。")
        values, choices = payload.get("regions"), payload.get("revisions")
        if not isinstance(values, dict) or set(values) != set(self.regions):
            raise ValueError("Word 编辑内容不完整或不属于当前文件，未写入原文件。")
        if not isinstance(choices, dict) or any(key not in self.revisions or value not in {"accept", "reject"} for key, value in choices.items()):
            raise ValueError("Word 修订操作无效，未写入原文件。")
        if any(local(self.revisions[key]) in COMPLEX_REVISIONS for key in choices):
            raise ValueError("移动、单元格合并或编号修订需在 Word 中处理；原文件未修改。")
        if any(not isinstance(value, str) or len(value) > 2_000_000 or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value) for value in values.values()):
            raise ValueError("Word 文字包含无效字符或超出编辑长度。")
        changed = set()
        for identifier, value in values.items():
            original = self.snapshot["regions"][identifier]
            if value == original:
                continue
            nodes, paragraph = self.regions[identifier]
            changed.add(self.nodes[self.ids[paragraph]][0])
            if not nodes:
                run = etree.SubElement(paragraph, tag("r"))
                node = etree.SubElement(run, tag("t"))
                self._set_text(node, value)
                continue
            starts, offset = [], 0
            for node in nodes:
                starts.append(offset)
                offset += len(node.text or "")
            replacements = ["" for _node in nodes]
            for operation, a, b, c, d in difflib.SequenceMatcher(None, original, value).get_opcodes():
                if operation == "equal":
                    for index, node in enumerate(nodes):
                        left, right = max(a, starts[index]), min(b, starts[index] + len(node.text or ""))
                        if left < right:
                            replacements[index] += original[left:right]
                elif operation in {"replace", "insert"}:
                    index = max(0, min(len(nodes) - 1, bisect.bisect_right(starts, a) - 1))
                    replacements[index] += value[c:d]
            for node, replacement in zip(nodes, replacements):
                if replacement != (node.text or ""):
                    self._set_text(node, replacement)
        # Children are resolved first. Discarding a parent revision then also
        # discards its edited contents, exactly as the chosen review operation.
        for identifier in reversed(list(self.revisions)):
            if identifier in choices:
                node = self.revisions[identifier]
                if node.getparent() is not None:
                    self._resolve_revision(node, choices[identifier])
                    changed.add(self.nodes[identifier][0])
        if not changed:
            return self.source
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(self.source)) as original, zipfile.ZipFile(output, "w") as archive:
            archive.comment = original.comment
            for info in original.infolist():
                content = self.parts[info.filename]
                if info.filename in changed:
                    content = etree.tostring(self.roots[info.filename], encoding="UTF-8", xml_declaration=True)
                archive.writestr(info, content)
        log.info("docx_review.serialize parts=%s text_regions=%s revisions=%s", len(changed),
                 sum(values[key] != value for key, value in self.snapshot["regions"].items()), len(choices))
        return output.getvalue()

    @staticmethod
    def _set_text(node, value):
        pieces = re.split(r"([\n\t])", value.replace("\r\n", "\n").replace("\r", "\n"))
        parent = node.getparent()
        index = parent.index(node)
        for offset, piece in enumerate(pieces):
            child = copy.deepcopy(node) if offset == 0 or piece not in {"\n", "\t"} else etree.Element(tag("br" if piece == "\n" else "tab"))
            if piece not in {"\n", "\t"}:
                child.text = piece
                child.set(XML_SPACE, "preserve")
            parent.insert(index + offset, child)
        parent.remove(node)

    @staticmethod
    def _resolve_revision(node, decision):
        name, parent = local(node), node.getparent()
        if name.endswith("Change"):
            if decision == "reject":
                previous = next((child for child in node if child.tag == parent.tag), None)
                if previous is None:
                    raise ValueError("这项格式修订缺少原属性，无法拒绝；原文件未修改。")
                parent.getparent().replace(parent, copy.deepcopy(previous))
            else:
                parent.remove(node)
            return
        keep = (name in {"ins", "moveTo"}) == (decision == "accept")
        if word(parent, "trPr"):
            row = parent.getparent()
            if keep:
                parent.remove(node)
            else:
                table = row.getparent()
                table.remove(row)
                if not table.findall(tag("tr")):
                    table.getparent().remove(table)
        elif word(parent, "rPr") and parent.getparent() is not None and word(parent.getparent(), "pPr"):
            paragraph = parent.getparent().getparent()
            parent.remove(node)
            if not keep:
                following = paragraph.getnext()
                if following is None or not word(following, "p"):
                    raise ValueError("段落标记修订缺少相邻段落，无法合并；原文件未修改。")
                # The surviving paragraph mark supplies the paragraph properties.
                index = 1 if following.find(tag("pPr")) is not None else 0
                for child in list(paragraph):
                    if not word(child, "pPr"):
                        following.insert(index, child)
                        index += 1
                paragraph.getparent().remove(paragraph)
        elif keep:
            index = parent.index(node)
            for child in list(node):
                parent.insert(index, child)
                for deleted_text in child.iter():
                    if local(deleted_text) in {"delText", "delInstrText"} and not any(
                        local(ancestor) in {"del", "moveFrom"} for ancestor in deleted_text.iterancestors()
                    ):
                        deleted_text.tag = tag("t" if local(deleted_text) == "delText" else "instrText")
                index += 1
            parent.remove(node)
        else:
            parent.remove(node)


def load_review(source):
    return WordReviewDocument(source).snapshot


def save_review(source, payload):
    return WordReviewDocument(source).save(payload)
