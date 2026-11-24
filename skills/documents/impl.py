import base64
import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
from contextlib import closing, contextmanager

from core.filesystem_ops import _build_error, _build_ok, record_full_read_state, resolve_path


SUPPORTED_EXTENSIONS = {".docx", ".pptx", ".xlsx", ".xls", ".pdf"}
LOGGER = logging.getLogger(__name__)


class DocumentChangedError(ValueError):
    pass


def _bundled_dependency_error(package_name, exc):
    raise RuntimeError(
        f"随应用安装的文档读取组件缺少 {package_name}。请重新安装完整的 Cowork 分发包。"
    ) from exc


def _get_openpyxl():
    try:
        import openpyxl

        return openpyxl
    except ImportError as exc:
        _bundled_dependency_error("openpyxl", exc)


def _get_docx_document():
    try:
        from docx import Document

        return Document
    except ImportError as exc:
        _bundled_dependency_error("python-docx", exc)


def _get_presentation():
    try:
        from pptx import Presentation

        return Presentation
    except ImportError as exc:
        _bundled_dependency_error("python-pptx", exc)


def _get_pdf_reader():
    try:
        from pypdf import PdfReader

        return PdfReader
    except ImportError as exc:
        _bundled_dependency_error("pypdf", exc)


def _fingerprint(path):
    stat = os.stat(path)
    value = f"{os.path.normcase(os.path.realpath(path))}\0{stat.st_size}\0{stat.st_mtime_ns}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _encode_cursor(identity, selection, position, offset):
    raw = json.dumps([1, identity, selection, position, offset], ensure_ascii=False, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor):
    try:
        if not isinstance(cursor, str) or len(cursor) > 16000:
            raise ValueError()
        version, identity, selection, position, offset = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if (version != 1 or not isinstance(identity, str) or len(identity) != 64
                or not isinstance(selection, list) or len(selection) != 3
                or any(value is not None and not isinstance(value, str) for value in selection)
                or not isinstance(position, list) or len(position) not in (1, 2)
                or any(type(value) is not int or value < 0 for value in position)
                or type(offset) is not int or offset < 0):
            raise ValueError()
        return identity, selection, position, offset
    except Exception as exc:
        raise ValueError("无效的续读位置，请重新读取文档。") from exc


def _page_range(pages, total):
    if not pages:
        return range(total)
    parts = str(pages).split("-")
    if len(parts) not in (1, 2):
        raise ValueError("pages 应为 'N' 或 'N-M'。")
    start, end = int(parts[0]), int(parts[-1])
    if start <= 0 or end < start or start > total:
        raise ValueError("页码范围无效或超出文档。")
    return range(start - 1, min(end, total))


@contextmanager
def _document_units(path, ext, selection, position):
    """Yield metadata and a lazy stream of (position, locator, text)."""
    sheet_name, pages, cell_range = selection
    resource = None
    try:
        if ext in {".xlsx", ".xls"}:
            if len(position) != 2:
                raise ValueError("Excel 续读位置无效。")
            bounds = None
            if cell_range:
                bounds = _get_openpyxl().utils.cell.range_boundaries(cell_range)
                if any(value is None or value < 1 for value in bounds) or bounds[0] > bounds[2] or bounds[1] > bounds[3]:
                    raise ValueError("cell_range 应为完整单元格范围，例如 A1:D100。")
            if ext == ".xlsx":
                resource = _get_openpyxl().load_workbook(path, read_only=True, data_only=True)
                names = resource.sheetnames
            else:
                try:
                    import xlrd
                except ImportError as exc:
                    _bundled_dependency_error("xlrd", exc)
                resource = xlrd.open_workbook(path, on_demand=True)
                names = resource.sheet_names()
            if sheet_name and sheet_name not in names:
                raise ValueError(f"找不到工作表：{sheet_name}")
            selected = [sheet_name] if sheet_name else names
            if position[0] >= len(selected):
                raise ValueError("工作表续读位置超出范围。")

            def excel_units():
                for sheet_index in range(position[0], len(selected)):
                    name = selected[sheet_index]
                    sheet = resource[name] if ext == ".xlsx" else resource.sheet_by_name(name)
                    max_row, max_col = ((sheet.max_row, sheet.max_column) if ext == ".xlsx" else (sheet.nrows, sheet.ncols))
                    first_col, first_row, last_col, last_row = bounds or (1, 1, max_col, max_row)
                    if max_row is not None:
                        last_row = min(last_row, max_row) if last_row is not None else max_row
                    if max_col is not None:
                        last_col = min(last_col, max_col) if last_col is not None else max_col
                    start_row = position[1] if sheet_index == position[0] else 0
                    if last_row is not None and start_row > last_row and start_row != 0:
                        raise ValueError("行续读位置超出范围。")
                    if start_row == 0:
                        yield [sheet_index, 0], {"type": "sheet", "name": name}, f"Sheet {name}:\n"
                    start_row = max(first_row, start_row)
                    if (last_row is None or start_row <= last_row) and (last_col is None or first_col <= last_col):
                        if ext == ".xlsx":
                            rows = sheet.iter_rows(min_row=start_row, max_row=last_row, min_col=first_col,
                                                   max_col=last_col, values_only=True)
                        else:
                            rows = (sheet.row_values(row, first_col - 1, last_col) for row in range(start_row - 1, last_row))
                        try:
                            for row_number, values in enumerate(rows, start_row):
                                text = "\t".join("" if value is None else str(value) for value in values) + "\n"
                                yield [sheet_index, row_number], {"type": "row", "name": name, "index": row_number,
                                                                "first_column": first_col}, text
                        finally:
                            rows.close()
                    if ext == ".xls":
                        resource.unload_sheet(name)

            yield {"sheet_count": len(names)}, excel_units()
        else:
            if len(position) != 1:
                raise ValueError("文档续读位置无效。")
            start = position[0]
            if ext == ".docx":
                document = _get_docx_document()(path)
                paragraphs = document.paragraphs
                if start > len(paragraphs):
                    raise ValueError("段落续读位置超出范围。")
                units = (([index], {"type": "paragraph", "index": index + 1}, paragraphs[index].text + "\n")
                         for index in range(start, len(paragraphs)))
                yield {"paragraph_count": len(paragraphs)}, units
            elif ext == ".pptx":
                presentation = _get_presentation()(path)
                if start > len(presentation.slides):
                    raise ValueError("幻灯片续读位置超出范围。")
                def slide_units():
                    for index in range(start, len(presentation.slides)):
                        text = "\n".join(shape.text for shape in presentation.slides[index].shapes if getattr(shape, "text", ""))
                        yield [index], {"type": "slide", "index": index + 1}, f"Slide {index + 1}:\n{text}\n\n"
                yield {"slide_count": len(presentation.slides)}, slide_units()
            else:
                resource = open(path, "rb")
                reader = _get_pdf_reader()(resource)
                indices = _page_range(pages, len(reader.pages))
                if start > len(indices):
                    raise ValueError("页续读位置超出范围。")
                def pdf_units():
                    for unit in range(start, len(indices)):
                        index = indices[unit]
                        text = reader.pages[index].extract_text() or ""
                        yield [unit], {"type": "page", "index": index + 1}, f"Page {index + 1}:\n{text}\n\n"
                yield {"total_pages": len(reader.pages), "returned_pages": 0}, pdf_units()
    finally:
        if resource is not None:
            if ext == ".xls":
                resource.release_resources()
            else:
                resource.close()


def document_read(workspace_dir, path, sheet_name=None, pages=None, max_chars=12000, _context=None,
                  cursor=None, cell_range=None):
    action = "document_read"
    ext = os.path.splitext(str(path or ""))[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return _build_error(action, "unsupported_format", "Use document_read for DOCX/PPTX/XLSX/XLS/PDF; text_file_read for plain text.")
    abs_path, rel_path, error = resolve_path(workspace_dir, path, context=_context, action=action, must_exist=True)
    if error:
        return error
    if not os.path.isfile(abs_path):
        return _build_error(action, "not_a_file", "Path is not a file.", path=rel_path)
    context = _context if isinstance(_context, dict) else {}
    trace = f"session_id={context.get('session_id', '')} tool_call_id={context.get('tool_call_id', '')}"
    try:
        LOGGER.info("document_read stage=start %s format=%s continuation=%s", trace, ext, bool(cursor))
        if type(max_chars) is not int or not 2000 <= max_chars <= 60000:
            raise ValueError("max_chars 必须是 2000–60000 的整数。")
        identity = _fingerprint(abs_path)
        selection = [sheet_name or None, pages or None, cell_range or None]
        position, offset = ([0, 0] if ext in {".xlsx", ".xls"} else [0]), 0
        if cursor:
            previous_identity, previous_selection, position, offset = _decode_cursor(cursor)
            if identity != previous_identity:
                raise DocumentChangedError("文件已变化或续读位置属于其他文件，请重新读取。")
            if any(value is not None and value != previous_selection[index] for index, value in enumerate(selection)):
                raise ValueError("续读时不能更改读取范围，请移除 cursor 后重新读取。")
            selection = previous_selection
        if (selection[0] or selection[2]) and ext not in {".xlsx", ".xls"}:
            raise ValueError("sheet_name 和 cell_range 仅用于 Excel。")
        if selection[1] and ext != ".pdf":
            raise ValueError("pages 仅用于 PDF。")
        with _document_units(abs_path, ext, selection, position) as (metadata, units), closing(units):
            result = _build_ok(action, {"path": rel_path, "format": ext[1:], "content": "", "metadata": metadata,
                                        "sections": [], "range": None, "truncated": False, "has_more": False, "next_cursor": None})
            # Bound the actual JSON sent to the model, including duplicated section text.
            def fits(candidate):
                return len(json.dumps(candidate, ensure_ascii=False)) <= max_chars

            if not fits(result):
                raise ValueError("元数据超出 max_chars，请提高上限。")
            for unit_position, locator, text in units:
                start_offset = offset if unit_position == position else 0
                if start_offset > len(text):
                    raise ValueError("续读字符位置超出范围。")
                if start_offset == len(text):
                    continue
                batch_metadata = dict(result["metadata"])
                if "name" in locator:
                    batch_metadata["sheet_names"] = list(dict.fromkeys(batch_metadata.get("sheet_names", []) + [locator["name"]]))
                if locator["type"] == "page":
                    batch_metadata["returned_pages"] = batch_metadata.get("returned_pages", 0) + 1

                def candidate(length):
                    fragment = text[start_offset:start_offset + length]
                    section = dict(locator, offset=start_offset, text=fragment)
                    sections = result["sections"] + [section]
                    return dict(result, content=result["content"] + fragment, metadata=batch_metadata, sections=sections,
                                range={"first": {k: v for k, v in sections[0].items() if k != "text"},
                                       "last": dict(locator, offset=start_offset + length)},
                                truncated=True, has_more=True,
                                next_cursor=_encode_cursor(identity, selection, unit_position, start_offset + length))

                low, high = 0, min(len(text) - start_offset, max_chars)
                while low < high:
                    middle = (low + high + 1) // 2
                    if fits(candidate(middle)):
                        low = middle
                    else:
                        high = middle - 1
                if low == 0:
                    if not result["content"]:
                        raise ValueError("本批定位信息超出 max_chars，请提高上限或缩小读取范围。")
                    break
                result = candidate(low)
                if start_offset + low < len(text):
                    break
            else:
                result.update(truncated=False, has_more=False, next_cursor=None)
            if _fingerprint(abs_path) != identity:
                raise DocumentChangedError("读取期间文件发生变化，请重新读取。")
            if not cursor and not any(selection) and not result["has_more"]:
                record_full_read_state(abs_path, _context)
            LOGGER.info("document_read stage=completed %s format=%s chars=%s has_more=%s", trace, ext,
                        len(json.dumps(result, ensure_ascii=False)), result["has_more"])
            return result
    except Exception as exc:
        LOGGER.warning("document_read stage=failed %s format=%s error_type=%s", trace, ext, type(exc).__name__)
        code = "file_changed" if isinstance(exc, DocumentChangedError) else ("invalid_argument" if isinstance(exc, ValueError) else "read_failed")
        return _build_error(action, code, str(exc)[:500])


class _TaskCancellation:
    def __init__(self, context):
        from core.execution_authorization import current_authorization
        auth = current_authorization(context)
        self.event = auth._cancelled if auth else threading.Event()
        self.abort = context.get("abort_check")

    def is_set(self):
        return self.event.is_set() or bool(callable(self.abort) and self.abort())

    def wait(self, timeout):
        return self.event.wait(timeout) or self.is_set()


def document_copy(workspace_dir, path, _context=None):
    """Create a new task copy; never provide an original-overwrite operation."""
    from core.document_preview import source_bytes
    context=_context or {}
    session=str(context.get('session_id') or context.get('conversation_id') or '')
    run=str(context.get('run_id') or '')
    try:
        if not session or not run: raise ValueError('缺少任务或会话，无法创建修改稿。')
        source,display,error=resolve_path(workspace_dir,path,context=context,action='document_copy',must_exist=True)
        if error: return error
        if os.path.splitext(source)[1].lower() not in SUPPORTED_EXTENSIONS: raise ValueError('此工具仅为 Office/PDF 创建任务副本。')
        if _TaskCancellation(context).is_set(): raise ValueError('任务已停止，未创建修改稿。')
        data=source_bytes(source)
        version=hashlib.sha256(data).hexdigest()
        task=hashlib.sha256((session+'\0'+run+'\0'+os.path.normcase(source)+'\0'+version).encode()).hexdigest()[:32]
        folder,_,error=resolve_path(workspace_dir,'.cowork/office-drafts/'+task,context={},action='document_copy',for_write=True)
        if error: return error
        os.makedirs(folder,exist_ok=True)
        name,extension=os.path.splitext(os.path.basename(source))
        target=os.path.join(folder,name+'-修改稿'+extension)
        if os.path.normcase(os.path.realpath(target))!=os.path.normcase(os.path.abspath(target)):
            raise ValueError('修改稿路径包含重定向，未写入文件。')
        existed=os.path.exists(target)
        if not existed:
            # Publish only a complete copy, without replacing another call's file.
            fd,temporary=tempfile.mkstemp(prefix='.copy-',dir=folder)
            try:
                with os.fdopen(fd,'wb') as handle:
                    handle.write(data);handle.flush();os.fsync(handle.fileno())
                try:
                    if os.name=='nt': os.rename(temporary,target)  # Windows rename is exclusive, including FAT/SMB.
                    else: os.link(temporary,target)
                except FileExistsError: existed=True
            finally:
                if os.path.exists(temporary): os.unlink(temporary)
        LOGGER.info('document_copy stage=completed session_id=%s run_id=%s version=%s reused=%s',session,run,version[:12],existed)
        return _build_ok('document_copy',{'path':os.path.relpath(target,workspace_dir).replace('\\','/'),
            'source_version':version,'reused':existed,'content':'继续修改此任务副本。原件未写入；默认另存流程不等于操作系统写入隔离。'})
    except Exception as exc:
        LOGGER.warning('document_copy stage=failed session_id=%s run_id=%s reason=%s',session,run,type(exc).__name__)
        return _build_error('document_copy',getattr(exc,'code','copy_failed'),str(exc)[:500])


def document_render(workspace_dir, path, pages=None, source_version=None, renderer='auto', review=False,
                    constraints='', review_kind='details', excel_options=None, _context=None):
    """Create bounded page images for view_image without changing the document."""
    from core.document_preview import render_document_pages, session_preview_root
    context = _context or {}
    session = str(context.get("session_id") or context.get("conversation_id") or "")
    trace = f"session_id={session} tool_call_id={context.get('tool_call_id', '')}"
    output = None
    preserve_evidence = False
    LOGGER.info("document_render stage=start %s pages=%s", trace, pages or "first-batch")
    try:
        if not session:
            LOGGER.warning("document_render stage=failed %s reason=session_unavailable", trace)
            return _build_error("document_render", "session_unavailable", "缺少当前会话，无法保存页面预览。")
        source, display_path, error = resolve_path(workspace_dir, path, context=context, action="document_render", must_exist=True)
        if error:
            LOGGER.warning("document_render stage=failed %s reason=%s", trace, error.get("error"))
            return error
        if type(review) is not bool:
            raise ValueError('review 必须为布尔值。')
        timeout = 120
        if review:
            from core.document_review import begin_review
            timeout = min(timeout,begin_review(context))
        session_key = hashlib.sha256(session.encode("utf-8")).hexdigest()[:24]
        # No caller-supplied output path, and no junction/symlink traversal.
        destination, _, error = resolve_path(workspace_dir, f".cowork/document-previews/{session_key}",
                                              context={}, action="document_render", for_write=True)
        if error:
            LOGGER.warning("document_render stage=failed %s reason=%s", trace, error.get("error"))
            return error
        if review:
            destination = str(session_preview_root(session) / 'evidence')
        cancelled = _TaskCancellation(context)
        if cancelled.is_set():
            LOGGER.info("document_render stage=cancelled %s", trace)
            return _build_error("document_render", "cancelled", "已停止页面渲染，源文件未修改。")
        os.makedirs(destination, exist_ok=True)
        output = tempfile.mkdtemp(prefix="pages-", dir=destination)
        result = render_document_pages(source, output, pages=pages, source_version=source_version, cancel=cancelled,
            renderer=renderer, pdf_root=session_preview_root(session) / 'pdf',timeout=timeout,kind=review_kind,excel_options=excel_options)
        check = None
        if review:
            from core.document_review import review_rendered_batch
            preserve_evidence = True
            check = review_rendered_batch(source, result, output, {**context, 'abort_check': cancelled.is_set},
                constraints=constraints, kind=review_kind)
            if check.get('reused'):
                shutil.rmtree(output)
        for page in result["pages"]:
            filename = page.pop('file')
            if not review:
                page['path'] = os.path.relpath(os.path.join(output, filename), workspace_dir).replace('\\', '/')
        result.pop('pdf_path', None)
        LOGGER.info("document_render stage=completed %s version=%s pages=%s", trace, result["source_version"][:12], len(result["pages"]))
        return _build_ok("document_render", {"path": display_path, **result,
            **({'visual_review': check} if review else {}),
            "content": ('页面图片与检查记录已保存。以 visual_review 的实际覆盖、问题和未查页为准；原图不进入主对话。'
                if review else '页面图片已生成；可用 review=true 发起独立视觉检查。生成成功不代表已检查。')})
    except Exception as exc:
        cleanup_error = ""
        if output and not preserve_evidence:
            try:
                shutil.rmtree(output)
            except OSError:
                LOGGER.exception("document_render stage=cleanup_failed %s", trace)
                cleanup_error = " 部分预览缓存未能清理，源文件未修改。"
        code = getattr(exc, "code", "invalid_argument" if isinstance(exc, ValueError) else "render_failed")
        LOGGER.warning("document_render stage=failed %s reason=%s", trace, code)
        return _build_error("document_render", code, str(exc)[:500] + cleanup_error)


TOOL_EXPORTS = [{
    "name": "document_read", "handler": document_read,
    "description": "Read DOCX, PPTX, XLSX, XLS or PDF in bounded batches. Read only needed ranges; pass next_cursor as cursor to continue. Use text_file_read for plain text.",
    "parameters": {"type": "object", "properties": {
        "path": {"type": "string", "description": "Workspace-relative document path."},
        "sheet_name": {"type": "string", "description": "Optional Excel sheet. Default: iterate sheets in workbook order."},
        "pages": {"type": "string", "description": "Optional PDF page selector: '1' or '2-4'."},
        "cell_range": {"type": "string", "description": "Optional Excel range, e.g. A1:D100, applied to selected sheets."},
        "cursor": {"type": "string", "description": "Opaque next_cursor from prior result; keep path, omit or keep identical selectors."},
        "max_chars": {"type": "integer", "minimum": 2000, "maximum": 60000,
                      "description": "Whole JSON result character budget including metadata and sections. Default 12000."},
    }, "required": ["path"]},
    "read_only": True, "search_hint": "read docx pptx xlsx xls pdf office document structured batch range",
}, {
    "name": "document_render", "handler": document_render,
    "description": "Render Office/PDF pages without editing the source. review=true sends at most 4 images to a separate visual request and returns verified page coverage and findings, without images in chat history. Local Office/WPS or LibreOffice is required for Office. Keep source_version, renderer and exact layout constraints on later batches. Review budgets span all files and versions in the run; stop on a budget limit and deliver the latest draft with remaining issues.",
    "parameters": {"type": "object", "properties": {
        "path": {"type": "string", "description": "Document path; relative to current workspace or an authorized absolute path."},
        "pages": {"type": "string", "description": "One-based pages/ranges, e.g. '2-4' or '1,8,16,24'; at most 4 details or 24 overview pages. Overview default samples across the entire document."},
        "source_version": {"type": "string", "description": "Source hash returned by an earlier batch; rejects changed documents."},
        'renderer': {'type': 'string', 'enum': ['auto', 'office', 'wps', 'libreoffice'], 'description': 'Choose once per task. A failed selected renderer is never silently replaced.'},
        'review': {'type': 'boolean', 'description': 'Use true for independent visual review; false only renders images.'},
        'constraints': {'type': 'string', 'description': 'Short exact layout requirements, including fonts, sizes, colors, template and exceptions. Repeat unchanged across batches.'},
        'review_kind': {'type': 'string', 'enum': ['details', 'overview'], 'description': 'details checks legibility and clipping; overview checks consistency and does not count as detail coverage.'},
        'excel_options': {'type':'object','properties':{
            'mode':{'type':'string','enum':['region','print']},'sheet':{'type':'string'},
            'range':{'type':'string','description':'A1:R80; omit for used cells.'},
            'header_rows':{'type':'string','description':'Optional repeated header rows, e.g. 1:2. Omit to retain existing print title rows.'},
            'identity_columns':{'type':'string','description':'Explicit row identity columns repeated on each slice, e.g. A:B. Omit if none.'}},'required':['mode'],
            'description':'Excel region reading splits wide tables before rasterization; print preserves original pagination. Defaults to original print if omitted.'},
    }, "required": ["path"]},
    "read_only": True, "destructive": False,
    "search_hint": "render inspect docx pptx pdf page slide layout PNG 文档 页面 幻灯片 排版 检查",
}, {
    'name':'document_copy','handler':document_copy,
    'description':'Create or reuse an Office/PDF task copy before editing an existing file. Only writes a new application-chosen draft path; never overwrites the original or an existing edited copy. This is a default workflow, not an OS write sandbox.',
    'parameters':{'type':'object','properties':{'path':{'type':'string','description':'Source document path.'}},'required':['path']},
    'read_only':False,'destructive':False,'search_hint':'office draft copy preserve original 修改稿 副本',
}]
