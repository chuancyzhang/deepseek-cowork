import hashlib
import json
import os
import re
import sys
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


def office_preview_cache_path(path, cache_root):
    digest = hashlib.sha256(deliverable_fingerprint(path).encode("utf-8")).hexdigest()
    stem = os.path.splitext(os.path.basename(path))[0]
    safe_stem = re.sub(r"[^\w.-]+", "_", stem, flags=re.UNICODE).strip("._") or "document"
    directory = os.path.join(os.path.abspath(cache_root), "office_previews")
    return os.path.join(directory, f"{safe_stem}-{digest[:20]}.pdf")


def office_export_command(source_path, output_path, main_script=None):
    if getattr(sys, "frozen", False):
        return [sys.executable, "--office-preview-export", source_path, output_path]
    script = os.path.abspath(main_script or os.path.join(os.path.dirname(os.path.dirname(__file__)), "main.py"))
    return [sys.executable, script, "--office-preview-export", source_path, output_path]


def _require_ax_object(prog_id):
    from PySide6.QtAxContainer import QAxObject

    obj = QAxObject(prog_id)
    if obj.isNull():
        raise RuntimeError(f"无法启动 {prog_id}，请确认已安装对应的 Microsoft Office 桌面应用。")
    return obj


def export_office_to_pdf(source_path, output_path):
    source = os.path.abspath(source_path)
    output = os.path.abspath(output_path)
    if not os.path.isfile(source):
        raise FileNotFoundError(f"源文件不存在：{source}")
    ext = os.path.splitext(source)[1].lower()
    kind = OFFICE_EXTENSIONS.get(ext, "")
    if not kind:
        raise ValueError(f"不支持使用 Office 预览的格式：{ext or '无扩展名'}")
    os.makedirs(os.path.dirname(output), exist_ok=True)
    if os.path.exists(output):
        os.remove(output)

    application = None
    document = None
    try:
        if kind == "word":
            application = _require_ax_object("Word.Application")
            application.setProperty("Visible", False)
            application.setProperty("DisplayAlerts", 0)
            documents = application.querySubObject("Documents")
            document = documents.querySubObject("Open(const QString&, bool, bool)", source, False, True)
            if document is None or document.isNull():
                raise RuntimeError("Word 无法以只读方式打开该文件。")
            document.dynamicCall("ExportAsFixedFormat(const QString&, int)", output, 17)
        elif kind == "powerpoint":
            application = _require_ax_object("PowerPoint.Application")
            presentations = application.querySubObject("Presentations")
            document = presentations.querySubObject("Open(const QString&, bool, bool, bool)", source, True, False, False)
            if document is None or document.isNull():
                raise RuntimeError("PowerPoint 无法以只读方式打开该文件。")
            document.dynamicCall("SaveAs(const QString&, int)", output, 32)
        else:
            application = _require_ax_object("Excel.Application")
            application.setProperty("Visible", False)
            application.setProperty("DisplayAlerts", False)
            workbooks = application.querySubObject("Workbooks")
            document = workbooks.querySubObject("Open(const QString&, int, bool)", source, 0, True)
            if document is None or document.isNull():
                raise RuntimeError("Excel 无法以只读方式打开该文件。")
            document.dynamicCall("ExportAsFixedFormat(int, const QString&)", 0, output)
        if not os.path.isfile(output) or os.path.getsize(output) <= 0:
            raise RuntimeError("Microsoft Office 未生成有效的 PDF 预览文件。")
        return output
    finally:
        if document is not None:
            try:
                document.dynamicCall("Close(bool)", False)
            except Exception:
                pass
        if application is not None:
            try:
                application.dynamicCall("Quit()")
            except Exception:
                pass


def run_office_export_cli(source_path, output_path):
    try:
        result = export_office_to_pdf(source_path, output_path)
        print(json.dumps({"ok": True, "path": result}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
