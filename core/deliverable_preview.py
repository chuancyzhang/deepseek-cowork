import html
import os
import re
from urllib.parse import quote


DELIVERABLE_TYPES = {
    ".html": ("html", "HTML", "fa5s.file-code"),
    ".htm": ("html", "HTML", "fa5s.file-code"),
    ".md": ("markdown", "Markdown", "fa5s.file-alt"),
    ".markdown": ("markdown", "Markdown", "fa5s.file-alt"),
    ".png": ("image", "图片", "fa5s.file-image"),
    ".jpg": ("image", "图片", "fa5s.file-image"),
    ".jpeg": ("image", "图片", "fa5s.file-image"),
    ".gif": ("image", "图片", "fa5s.file-image"),
    ".webp": ("image", "图片", "fa5s.file-image"),
    ".pdf": ("pdf", "PDF", "fa5s.file-pdf"),
    ".doc": ("doc", "DOC", "fa5s.file-word"),
    ".docx": ("docx", "DOCX", "fa5s.file-word"),
    ".ppt": ("ppt", "PPT", "fa5s.file-powerpoint"),
    ".pptx": ("pptx", "PPTX", "fa5s.file-powerpoint"),
    ".xls": ("xls", "XLS", "fa5s.file-excel"),
    ".xlsx": ("xlsx", "XLSX", "fa5s.file-excel"),
}

OFFICE_EXTENSIONS = {
    ".doc": "word",
    ".docx": "word",
    ".ppt": "powerpoint",
    ".pptx": "powerpoint",
    ".xls": "excel",
    ".xlsx": "excel",
}
_PATH_PATTERN = re.compile(
    r"(?P<path>(?:[A-Za-z]:[\\/]|\\\\)[^\r\n<>\"|?*]*?"
    r"(?:\.markdown|\.html|\.docx|\.pptx|\.xlsx|\.jpeg|\.webp|\.htm|\.pdf|\.doc|\.ppt|\.xls|\.png|\.jpg|\.gif|\.md))"
    r"(?=$|[\s\]\[(){}，。；：、,;:!?！？'\"])",
    re.IGNORECASE,
)


def normalize_workspace_file(path, workspace_dir):
    candidate = os.path.abspath(os.path.normpath(str(path or "").strip()))
    workspace = os.path.abspath(os.path.normpath(str(workspace_dir or "").strip()))
    if not candidate or not workspace or not os.path.isfile(candidate):
        return ""
    try:
        if os.path.commonpath([os.path.normcase(candidate), os.path.normcase(workspace)]) != os.path.normcase(workspace):
            return ""
    except ValueError:
        return ""
    if os.path.splitext(candidate)[1].lower() not in DELIVERABLE_TYPES:
        return ""
    return os.path.normpath(candidate)


def iter_workspace_file_paths(text, workspace_dir):
    results = []
    seen = set()
    for match in _PATH_PATTERN.finditer(str(text or "")):
        path = normalize_workspace_file(match.group("path"), workspace_dir)
        key = os.path.normcase(path)
        if path and key not in seen:
            results.append((match.start("path"), match.end("path"), path))
            seen.add(key)
    return results


def linkify_workspace_paths_in_html(html_text, workspace_dir):
    """Link supported paths in text nodes while leaving code and existing anchors intact."""
    from bs4 import BeautifulSoup, NavigableString

    soup = BeautifulSoup(str(html_text or ""), "html.parser")
    for node in list(soup.find_all(string=True)):
        if not isinstance(node, NavigableString):
            continue
        if node.parent and node.parent.name in {"a", "code", "pre", "script", "style"}:
            continue
        matches = iter_workspace_file_paths(str(node), workspace_dir)
        if not matches:
            continue
        cursor = 0
        replacements = []
        raw = str(node)
        for start, end, path in matches:
            if start > cursor:
                replacements.append(NavigableString(raw[cursor:start]))
            anchor = soup.new_tag("a")
            anchor["href"] = "cowork-file:" + quote(path, safe="")
            anchor["data-cowork-path"] = path
            anchor.string = raw[start:end]
            replacements.append(anchor)
            cursor = end
        if cursor < len(raw):
            replacements.append(NavigableString(raw[cursor:]))
        for replacement in reversed(replacements):
            node.insert_after(replacement)
        node.extract()
    return str(soup)


def deliverable_fingerprint(path):
    stat = os.stat(path)
    return f"{os.path.normcase(os.path.abspath(path))}|{stat.st_mtime_ns}|{stat.st_size}"


def _require_import(import_name, package_name):
    try:
        return __import__(import_name)
    except ImportError as exc:
        raise RuntimeError(f"内置预览缺少依赖 {package_name}，请重新安装或更新应用。") from exc


def _html_escape(value):
    return html.escape("" if value is None else str(value), quote=False)


def _preview_document(title, subtitle, body):
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
body {{
  margin: 0;
  padding: 24px;
  color: #1d1d1f;
  background: #f5f6f8;
  font-family: "Segoe UI", "Microsoft YaHei UI", Arial, sans-serif;
}}
main {{
  max-width: 980px;
  margin: 0 auto;
}}
.hero {{
  margin-bottom: 18px;
}}
h1 {{
  margin: 0 0 6px;
  font-size: 24px;
  font-weight: 750;
}}
.subtitle {{
  color: #636366;
  font-size: 13px;
}}
.section, .slide, .sheet {{
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid #e7e9ef;
  border-radius: 16px;
  margin: 14px 0;
  padding: 16px;
}}
.kicker {{
  color: #007aff;
  font-size: 11px;
  font-weight: 750;
  letter-spacing: 0;
  margin-bottom: 8px;
}}
p {{
  margin: 8px 0;
  line-height: 1.58;
  white-space: pre-wrap;
}}
table {{
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  overflow: hidden;
  border: 1px solid #e7e9ef;
  border-radius: 12px;
  background: #ffffff;
}}
td, th {{
  border-right: 1px solid #eef0f4;
  border-bottom: 1px solid #eef0f4;
  padding: 7px 9px;
  font-size: 12px;
  vertical-align: top;
}}
tr:last-child td, tr:last-child th {{
  border-bottom: none;
}}
td:last-child, th:last-child {{
  border-right: none;
}}
th {{
  background: #f7fbff;
  color: #636366;
  font-weight: 650;
}}
.empty, .note {{
  color: #8a8a8e;
  font-size: 13px;
}}
</style>
</head>
<body>
<main>
<div class="hero"><h1>{_html_escape(title)}</h1><div class="subtitle">{_html_escape(subtitle)}</div></div>
{body}
</main>
</body>
</html>"""


def _table_html(rows, header=False):
    if not rows:
        return '<p class="empty">没有可显示的表格内容。</p>'
    rendered = []
    for row_index, row in enumerate(rows):
        tag = "th" if header and row_index == 0 else "td"
        cells = "".join(f"<{tag}>{_html_escape(cell)}</{tag}>" for cell in row)
        rendered.append(f"<tr>{cells}</tr>")
    return "<table>" + "".join(rendered) + "</table>"


def _plain_table(rows):
    return "\n".join("\t".join("" if cell is None else str(cell) for cell in row) for row in rows)


def _truncate_items(items, limit):
    values = list(items or [])
    return values[:limit], len(values) > limit


def render_docx_preview(path, max_paragraphs=120, max_tables=12, max_table_rows=80, max_table_cols=12):
    docx = _require_import("docx", "python-docx")
    document = docx.Document(path)
    body = []
    text_parts = [f"DOCX 预览：{os.path.basename(path)}"]
    paragraphs = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
    visible_paragraphs, paragraphs_truncated = _truncate_items(paragraphs, max_paragraphs)
    if visible_paragraphs:
        paragraph_html = "".join(f"<p>{_html_escape(text)}</p>" for text in visible_paragraphs)
        body.append(f'<section class="section"><div class="kicker">正文</div>{paragraph_html}</section>')
        text_parts.extend(visible_paragraphs)
    tables, tables_truncated = _truncate_items(document.tables, max_tables)
    for index, table in enumerate(tables, start=1):
        rows = []
        for row in table.rows[:max_table_rows]:
            rows.append([cell.text.strip() for cell in row.cells[:max_table_cols]])
        body.append(f'<section class="section"><div class="kicker">表格 {index}</div>{_table_html(rows)}</section>')
        text_parts.append(f"\n表格 {index}\n{_plain_table(rows)}")
    notes = []
    if paragraphs_truncated:
        notes.append(f"正文较长，仅显示前 {max_paragraphs} 段。")
    if tables_truncated:
        notes.append(f"表格较多，仅显示前 {max_tables} 个。")
    if notes:
        body.append(f'<p class="note">{" ".join(_html_escape(note) for note in notes)}</p>')
        text_parts.extend(notes)
    if not body:
        body.append('<section class="section"><p class="empty">没有读取到可预览的文字内容。</p></section>')
    return {
        "format": "DOCX",
        "html": _preview_document(os.path.basename(path), "内置 DOCX 结构化预览", "\n".join(body)),
        "text": "\n\n".join(text_parts),
    }


def render_pptx_preview(path, max_slides=80):
    pptx = _require_import("pptx", "python-pptx")
    presentation = pptx.Presentation(path)
    body = []
    text_parts = [f"PPTX 预览：{os.path.basename(path)}"]
    slide_count = len(presentation.slides)
    for index, slide in enumerate(list(presentation.slides)[:max_slides], start=1):
        texts = []
        for shape in slide.shapes:
            if hasattr(shape, "text"):
                text = str(shape.text or "").strip()
                if text:
                    texts.append(text)
        slide_text = "\n".join(texts)
        text_parts.append(f"\nSlide {index}\n{slide_text}")
        if slide_text:
            body.append(
                f'<section class="slide"><div class="kicker">幻灯片 {index}</div>'
                + "".join(f"<p>{_html_escape(text)}</p>" for text in texts)
                + "</section>"
            )
        else:
            body.append(f'<section class="slide"><div class="kicker">幻灯片 {index}</div><p class="empty">没有文字内容。</p></section>')
    if slide_count > max_slides:
        note = f"幻灯片较多，仅显示前 {max_slides} 页。"
        body.append(f'<p class="note">{_html_escape(note)}</p>')
        text_parts.append(note)
    if not body:
        body.append('<section class="section"><p class="empty">没有读取到可预览的幻灯片内容。</p></section>')
    return {
        "format": "PPTX",
        "html": _preview_document(os.path.basename(path), f"内置 PPTX 结构化预览 · {slide_count} 页", "\n".join(body)),
        "text": "\n\n".join(text_parts),
    }


def render_xlsx_preview(path, max_sheets=8, max_rows=80, max_cols=16):
    openpyxl = _require_import("openpyxl", "openpyxl")
    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    body = []
    text_parts = [f"XLSX 预览：{os.path.basename(path)}"]
    sheet_names = workbook.sheetnames
    visible_sheets, sheets_truncated = _truncate_items(sheet_names, max_sheets)
    try:
        for name in visible_sheets:
            worksheet = workbook[name]
            rows = []
            for row_index, row in enumerate(worksheet.iter_rows(values_only=True), start=1):
                if row_index > max_rows:
                    break
                rows.append(["" if cell is None else cell for cell in list(row)[:max_cols]])
            body.append(f'<section class="sheet"><div class="kicker">工作表：{_html_escape(name)}</div>{_table_html(rows, header=True)}</section>')
            text_parts.append(f"\nSheet {name}\n{_plain_table(rows)}")
            if worksheet.max_row > max_rows or worksheet.max_column > max_cols:
                note = f"{name} 较大，仅显示前 {max_rows} 行、{max_cols} 列。"
                body.append(f'<p class="note">{_html_escape(note)}</p>')
                text_parts.append(note)
        if sheets_truncated:
            note = f"工作表较多，仅显示前 {max_sheets} 个。"
            body.append(f'<p class="note">{_html_escape(note)}</p>')
            text_parts.append(note)
    finally:
        workbook.close()
    if not body:
        body.append('<section class="section"><p class="empty">没有读取到可预览的表格内容。</p></section>')
    return {
        "format": "XLSX",
        "html": _preview_document(os.path.basename(path), f"内置 XLSX 结构化预览 · {len(sheet_names)} 个工作表", "\n".join(body)),
        "text": "\n\n".join(text_parts),
    }


def render_pdf_text_preview(path, max_pages=20, max_chars=60000):
    pypdf = _require_import("pypdf", "pypdf")
    reader = pypdf.PdfReader(path)
    chunks = [f"PDF 文本预览：{os.path.basename(path)}"]
    for page_index in range(min(len(reader.pages), max_pages)):
        page = reader.pages[page_index]
        chunks.append(f"\nPage {page_index + 1}\n{page.extract_text() or ''}")
    if len(reader.pages) > max_pages:
        chunks.append(f"\nPDF 页数较多，仅显示前 {max_pages} 页。")
    text = "\n".join(chunks)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n...内容较长，已截断显示。"
    return text


def render_structured_document_preview(path):
    source = os.path.abspath(path)
    if not os.path.isfile(source):
        raise FileNotFoundError(f"源文件不存在：{source}")
    ext = os.path.splitext(source)[1].lower()
    if ext == ".docx":
        return render_docx_preview(source)
    if ext == ".pptx":
        return render_pptx_preview(source)
    if ext == ".xlsx":
        return render_xlsx_preview(source)
    if ext in {".doc", ".ppt", ".xls"}:
        raise RuntimeError("旧版二进制 Office 格式暂不支持内置预览，请转换为 DOCX、PPTX 或 XLSX 后再预览。")
    raise ValueError(f"当前格式不支持内置文档预览：{ext or '无扩展名'}")
