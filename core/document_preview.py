"""Versioned PDF copies and read-only Office rendering, outside the UI thread."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
from io import BytesIO
import json
import logging
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile

from .env_utils import get_app_data_dir
from .process_utils import subprocess_kwargs_no_window
from .workbench import WorkbenchError

log = logging.getLogger(__name__)
DOCUMENT_RENDER_MAX_PAGES = 4
DOCUMENT_RENDER_LONG_EDGE = 2000
OFFICE_FAMILIES = {".doc": "word", ".docx": "word", ".ppt": "powerpoint",
                   ".pptx": "powerpoint", ".xls": "excel", ".xlsx": "excel"}
MAX_DOCUMENT_BYTES = 100 * 1024 * 1024


def cache_root():
    path = Path(get_app_data_dir()) / "browser_previews"
    path.mkdir(parents=True, exist_ok=True)
    return path


def session_preview_root(session_id):
    if not session_id:
        raise ValueError('缺少预览所属会话。')
    root = cache_root() / 'sessions' / hashlib.sha256(str(session_id).encode()).hexdigest()[:24]
    root.mkdir(parents=True, exist_ok=True)
    return root


def delete_session_previews(session_id):
    """Permanent conversation deletion only; workspace originals/drafts are separate."""
    if not session_id: raise ValueError('缺少会话。')
    root=cache_root().resolve()
    candidate=(root/'sessions'/hashlib.sha256(str(session_id).encode()).hexdigest()[:24]).resolve()
    if not candidate.is_relative_to(root/'sessions'): raise ValueError('缓存目录重定向，未删除文件。')
    if candidate.exists(): shutil.rmtree(candidate)


def source_bytes(path):
    before = os.stat(path)
    if before.st_size > MAX_DOCUMENT_BYTES:
        raise WorkbenchError("document_too_large", "文件超过 100 MiB，请使用系统应用打开。")
    with open(path, "rb") as stream:
        data = stream.read(MAX_DOCUMENT_BYTES + 1)
    if len(data) > MAX_DOCUMENT_BYTES:
        raise WorkbenchError("document_too_large", "文件超过 100 MiB，请使用系统应用打开。")
    after = os.stat(path)
    if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise WorkbenchError("source_changed", "文件仍在保存，请稍后刷新；已有预览保留。")
    return data


def cache_pdf(path, root=None, *, source_data=None):
    data = source_bytes(path) if source_data is None else source_data
    if not data.startswith(b"%PDF-"):
        raise WorkbenchError("invalid_pdf", "文件不是有效的 PDF。")
    root = Path(root or cache_root())
    root.mkdir(parents=True, exist_ok=True)
    target = root / (hashlib.sha256(data).hexdigest() + ".pdf")
    if not target.exists():
        temporary = root / (uuid.uuid4().hex + ".tmp")
        try:
            temporary.write_bytes(data)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return str(target)


def preview_diagnostics(source, pdf, *, source_data=None):
    """Observable font/size facts, without promising visual equivalence."""
    from pypdf import PdfReader
    from xml.etree import ElementTree
    try: reader=PdfReader(str(pdf))
    except Exception as exc: raise WorkbenchError('invalid_pdf','渲染结果不是有效的 PDF，已有预览保留，请重试或在本机应用查看。') from exc
    if not reader.pages: raise WorkbenchError('invalid_pdf','渲染结果没有页面。')
    warnings=[];unembedded=set()
    if len(reader.pages)>200: warnings.append('字体嵌入诊断仅覆盖前 200 页，其余页面仍需按检查范围核对。')
    for index in range(min(200,len(reader.pages))):
        page=reader.pages[index]
        resources=page.get('/Resources')
        if not resources: continue
        fonts=resources.get_object().get('/Font')
        if not fonts: continue
        for reference in fonts.get_object().values():
            font=reference.get_object()
            descendants=font.get('/DescendantFonts') or [font]
            for item in descendants:
                item=item.get_object();descriptor=item.get('/FontDescriptor')
                if item.get('/Subtype')=='/Type3': continue
                if not descriptor or not any(key in descriptor.get_object() for key in ('/FontFile','/FontFile2','/FontFile3')):
                    unembedded.add(str(item.get('/BaseFont') or font.get('/BaseFont') or 'unknown').lstrip('/'))
    if unembedded: warnings.append('PDF 中有未嵌入字体：'+ '、'.join(sorted(unembedded)[:8])+'。显示可能依赖阅读器字体，请核对关键页面。')
    source=Path(source)
    if source.suffix.lower() in {'.docx','.pptx','.xlsx'}:
        try:
            from .system_fonts import installed_font_families
            installed={name.casefold() for name in installed_font_families()}
            requested=set()
            with zipfile.ZipFile(source if source_data is None else BytesIO(source_data)) as package:
                for name in package.namelist():
                    if name=='word/document.xml' or name=='xl/styles.xml' or (name.startswith('ppt/slides/slide') and name.endswith('.xml')):
                        if package.getinfo(name).file_size>10*1024*1024: continue
                        for node in ElementTree.fromstring(package.read(name)).iter():
                            tag=node.tag.rsplit('}',1)[-1]
                            if tag=='rFonts': requested.update(value for key,value in node.attrib.items() if key.rsplit('}',1)[-1] in {'ascii','hAnsi','eastAsia'})
                            elif tag in {'latin','ea','cs'} and node.get('typeface'): requested.add(node.get('typeface'))
                            elif name=='xl/styles.xml' and tag=='name' and node.get('val'): requested.add(node.get('val'))
            missing=sorted(name for name in requested if name and not name.startswith('+') and name.casefold() not in installed)
            if missing: warnings.append('文件声明的部分字体在本机未找到：'+'、'.join(missing[:8])+'。可能发生字体替换，请核对版式。')
        except Exception as exc:
            log.warning('office_preview.font_inventory_unavailable reason=%s',type(exc).__name__)
            warnings.append('本次未能核对本机字体，请检查关键页面。')
    size=Path(pdf).stat().st_size
    if size > 10 * 1024 * 1024:
        source_size = source.stat().st_size if source_data is None else len(source_data)
        if size > 20 * max(1, source_size):
            warnings.append('预览副本明显大于原文件，可能包含大量字体或位图。')
    return warnings


# PDFium is not thread-safe. A bounded, cancellable process per batch avoids
# sharing native handles between concurrent conversations and the service.
_PDF_RASTER_SCRIPT = r'''
import json, math, pathlib, sys
try:
    import pypdfium2 as pdfium
    from PIL import Image, ImageDraw
    source, destination, selection, kind, long_edge = sys.argv[1:]
    long_edge = int(long_edge)
    doc = pdfium.PdfDocument(source)
    try:
        total = len(doc)
        numbers = json.loads(selection)
        if not numbers:
            count = min(total, 24 if kind == 'overview' else 4)
            numbers = ([round(i*(total-1)/max(1,count-1))+1 for i in range(count)]
                if kind == 'overview' else list(range(1,count+1)))
        if not numbers or min(numbers)<1 or max(numbers)>total:
            raise ValueError('页码超出文档范围。')
        pages = []
        contact = None
        for offset, number in enumerate(numbers):
            index = number-1
            page = doc[index]
            try:
                width, height = page.get_size()
                if not all(math.isfinite(v) and v > 0 for v in (width, height)):
                    raise ValueError('PDF 页面尺寸无效。')
                edge = 600 if kind == 'overview' else long_edge
                bitmap = page.render(scale=edge / max(width, height))
                try:
                    picture = bitmap.to_pil()
                    try:
                        # PDFium rounds up fractional page dimensions; keep the
                        # promised image budget even for 2000.0000001 pixels.
                        if max(picture.size)>edge: picture.thumbnail((edge,edge),Image.Resampling.LANCZOS)
                        if kind == 'overview':
                            if offset%6 == 0: contact = Image.new('RGB', (2000, 1400), '#eeeeee')
                            x, y = (offset%3)*666, ((offset%6)//3)*700
                            contact.paste(picture.convert('RGB'), (x+(666-picture.width)//2,y+40))
                            ImageDraw.Draw(contact).text((x+20,y+12), f'Page {number}', fill='black')
                            if offset%6 == 5 or offset == len(numbers)-1:
                                filename = f'overview-{offset//6+1}.png'
                                contact.save(str(pathlib.Path(destination)/filename))
                                pages.append(dict(page=numbers[offset-offset%6],page_numbers=numbers[offset-offset%6:offset+1],file=filename,width=2000,height=1400))
                                contact.close()
                        else:
                            filename = f'page-{number}.png'
                            picture.save(str(pathlib.Path(destination) / filename))
                            pages.append(dict(page=number, file=filename, width=picture.width, height=picture.height))
                    finally:
                        picture.close()
                finally:
                    bitmap.close()
            finally:
                page.close()
        last=max(numbers)
        print(json.dumps(dict(page_count=total, pages=pages, has_more=(len(numbers)<total if kind=='overview' else last<total),
                             next_pages=f'{last + 1}-{min(last + 4, total)}' if kind=='details' and last<total else None,
                             suggested_detail_pages=sorted(set([1,total]+[round(i*(total-1)/3)+1 for i in range(4)])))))
    finally:
        doc.close()
except Exception as exc:
    print(json.dumps(dict(error=type(exc).__name__, message=str(exc)[:400]), ensure_ascii=True))
    sys.exit(1)
'''


def render_document_pages(path, output_dir, *, pages=None, cancel=None, timeout=120, renderer='auto', pdf_root=None, kind='details', excel_options=None):
    """Render an immutable batch into a caller-owned directory; never edit source."""
    from .process_utils import popen_external_program, terminate_process_tree
    from .sandbox_runtime import get_runtime_executable
    import re

    cancel = cancel or threading.Event()
    if cancel.is_set():
        raise WorkbenchError("cancelled", "已停止页面渲染，源文件未修改。")
    if kind not in {'details','overview'}: raise ValueError('无效的检查类型。')
    numbers = []
    if pages is not None and str(pages).strip():
        for part in str(pages).split(','):
            match = re.fullmatch(r"([1-9][0-9]*)(?:-([1-9][0-9]*))?", part.strip())
            if not match: raise ValueError("pages 应为 '1'、'2-4' 或 '1,8,16,24'。")
            first,last=int(match[1]),int(match[2] or match[1])
            if last<first or last-first>23: raise ValueError('页码范围无效。')
            numbers.extend(range(first,last+1))
        if len(numbers)!=len(set(numbers)) or len(numbers)>(24 if kind=='overview' else 4):
            raise ValueError('细节每批最多 4 页，总览每批最多 24 页，不能重复页码。')
    source = Path(path).resolve()
    if source.suffix.lower() not in {*OFFICE_FAMILIES, '.pdf'}:
        raise ValueError("页面渲染支持 Office 和 PDF。")
    deadline = time.monotonic() + timeout
    data = source_bytes(source)
    if source.suffix.lower() == ".pdf":
        pdf = cache_pdf(source, root=pdf_root, source_data=data)
        fingerprint = Path(pdf).stem
        renderer = "PDFium"
    else:
        from .office_renderers import select_renderer
        selected = select_renderer(OFFICE_FAMILIES[source.suffix.lower()], renderer)
        pdf, fingerprint = render_office_pdf(
            source, cancel=cancel, timeout=timeout, renderer=selected['id'],
            root=pdf_root, excel_options=excel_options, source_data=data,
        )
        renderer = selected['name'] + ' + PDFium'
    if cancel.is_set():
        raise WorkbenchError("cancelled", "已停止页面渲染，源文件未修改。")
    if time.monotonic() >= deadline:
        raise WorkbenchError("renderer_timeout", "页面渲染超时，源文件未修改；请缩小页码范围后重试。")
    executable = get_runtime_executable("python")
    if not executable:
        raise WorkbenchError("runtime_unavailable", "应用 Python 运行时不可用，尚未完成页面渲染。")
    log.info("document_raster.start version=%s pages=%s renderer=%s", fingerprint[:12], pages or "first-batch", renderer)
    process = None
    try:
        process = popen_external_program(
            [executable, "-I", "-B", "-X", "utf8", "-c", _PDF_RASTER_SCRIPT,
             str(pdf), str(output_dir), json.dumps(numbers), kind, str(DOCUMENT_RENDER_LONG_EDGE)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            **subprocess_kwargs_no_window(),
        )
        while True:
            if cancel.is_set():
                raise WorkbenchError("cancelled", "已停止页面渲染，源文件未修改。")
            if time.monotonic() >= deadline:
                raise WorkbenchError("renderer_timeout", "页面渲染超时，源文件未修改；请缩小页码范围后重试。")
            try:
                stdout, stderr = process.communicate(timeout=.1)
                break
            except subprocess.TimeoutExpired:
                pass
        try:
            result = json.loads(stdout.decode("utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise WorkbenchError("renderer_failed", "页面渲染进程未返回有效结果，源文件未修改。") from exc
        if process.returncode or "error" in result:
            log.warning("document_raster.worker_failed exit=%s reason=%s", process.returncode, result.get("error"))
            message = result.get("message", "页面渲染失败。")
            if result.get("error") == "ModuleNotFoundError":
                message = "随包页面渲染组件缺失，请重新安装完整 Cowork 包。"
            raise WorkbenchError("renderer_failed", message)
        if cancel.is_set():
            raise WorkbenchError("cancelled", "已停止页面渲染，源文件未修改。")
        result.update(
            source_version=fingerprint, renderer=renderer, render_configuration=Path(pdf).stem,
            pdf_path=str(pdf), warnings=preview_diagnostics(source, pdf, source_data=data),
        )
        log.info("document_raster.finish version=%s pages=%s", fingerprint[:12], len(result["pages"]))
        return result
    except Exception as exc:
        log.warning("document_raster.failed version=%s reason=%s", fingerprint[:12], getattr(exc, "code", type(exc).__name__))
        raise
    finally:
        if process is not None:
            if process.poll() is None:
                terminate_process_tree(process)
            process.communicate()


_OFFICE_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$cfg = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__PAYLOAD__')) | ConvertFrom-Json
Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class CoworkWindowProcess { [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId); }'
$names = @{word='WINWORD'; excel='EXCEL'; powerpoint='POWERPNT'}
$app = $null; $doc = $null; $bootstrap = $null; $owned = $false
$wps = $false; $renderer = ''; $stage = 'activate'
$mutex = New-Object System.Threading.Mutex($false, ('Local\CoworkOfficePreview-' + $cfg.group))
$locked = $false
$clean = $true
$stateOwned = $false
function Assert-Active {
    if (Test-Path -LiteralPath $cfg.cancel) { throw '已取消版式预览，源文件未修改。' }
}
function Export-Workbook {
    if ($null -eq $cfg.excel -or $cfg.excel.mode -ne 'region') {
        $doc.ExportAsFixedFormat(0, [string]$cfg.target, 0, $false, $false)
        return
    }
    $part = 0
    foreach ($sheet in $doc.Worksheets) {
        if ($sheet.Visible -ne -1 -or ($cfg.excel.sheet -and $sheet.Name -ne $cfg.excel.sheet)) { continue }
        $area = $sheet.UsedRange
        if ($cfg.excel.range) { $area = $sheet.Range([string]$cfg.excel.range) }
        $top = $area.Row; $bottom = $top+$area.Rows.Count-1
        $left = $area.Column; $right = $left+$area.Columns.Count-1
        if ($area.Rows.Count -gt 2000 -or $area.Columns.Count -gt 100) { throw '区域过大，请指定不超过 2000 行、100 列的阅读范围。' }
        $setup = $sheet.PageSetup
        $setup.PaperSize = 9; $setup.Orientation = 2
        $setup.LeftMargin = 18; $setup.RightMargin = 18
        $setup.TopMargin = 30; $setup.BottomMargin = 30
        $setup.Zoom = 100; $setup.FitToPagesWide = $false; $setup.FitToPagesTall = $false
        $setup.PrintTitleColumns = [string]$cfg.excel.identity_columns
        if ($cfg.excel.header_rows) { $setup.PrintTitleRows = [string]$cfg.excel.header_rows }
        $identityWidth = if ($cfg.excel.identity_columns) { $sheet.Range([string]$cfg.excel.identity_columns).Width } else { 0 }
        if ($identityWidth -gt 300) { throw '重复标识列过宽，请减少列数。' }
        $start = $left
        while ($start -le $right) {
            Assert-Active
            $end = $start
            while ($end -lt $right -and ($sheet.Range($sheet.Cells.Item($top,$start),$sheet.Cells.Item($top,$end+1)).Width+$identityWidth) -le 730) { $end++ }
            # Keep merged column groups together. Oversized groups need an
            # explicit user range instead of silently slicing merged labels.
            foreach ($merge in $cfg.excel.merges) {
                if ($merge.sheet -eq $sheet.Name -and $merge.first -le $end -and $merge.last -gt $end -and $merge.last -le $right) { $end = $merge.last }
            }
            $slice = $sheet.Range($sheet.Cells.Item($top,$start),$sheet.Cells.Item($bottom,$end))
            if (($slice.Width+$identityWidth) -gt 1250) { throw '合并列组或单列过宽，请在表格视图阅读或指定较小范围。' }
            $setup.Zoom = [Math]::Min(100,[Math]::Floor(73000/($slice.Width+$identityWidth)))
            $setup.PrintArea = $slice.Address()
            $setup.CenterHeader = $sheet.Name + ' ' + $slice.Address()
            $part++
            if ($part -gt 40) { throw '阅读分片超过 40 份，请缩小范围。' }
            $sheet.ExportAsFixedFormat(0, [string]($cfg.target + '.part' + $part + '.pdf'), 0, $false, $false)
            $start = $end+1
        }
    }
    if ($part -eq 0) { throw '没有找到可读取的工作表，请核对表名。' }
    [Console]::Out.WriteLine('COWORK_PARTS:' + $part)
}
try {
    # PowerPoint is MultiUse: serialize our helpers without taking ownership of
    # an existing application or interfering with the user's presentations.
    Assert-Active
    try { $locked = $mutex.WaitOne(0) }
    catch [System.Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { throw '本机办公软件仍在转换或收尾，请重新检测后再试。' }
    if (Test-Path -LiteralPath $cfg.state) { throw '上次转换未确认清理完成，请关闭对应软件后重新检测。' }
    [IO.File]::WriteAllText([string]$cfg.state, (@{helper=$PID; renderer=$cfg.renderers[0]; phase='activate'} | ConvertTo-Json -Compress))
    $stateOwned = $true
    $ids = @(Get-Process -Name $names[$cfg.family] -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
    # Only discovery/activation may try another provider. Never retry a failed
    # document open/export with another engine, hiding the actual file error.
    foreach ($progId in $cfg.renderers) {
        Assert-Active
        try {
            $type = [Type]::GetTypeFromProgID([string]$progId, $false)
            if ($null -eq $type) { throw '自动化接口未注册' }
            $app = [Activator]::CreateInstance($type)
            if ($null -eq $app) { throw '自动化接口未返回应用实例' }
            $renderer = [string]$progId
            break
        } catch {
            [Console]::Out.WriteLine('COWORK_PROBE:' + $progId + ': ' + $_.Exception.Message)
        }
    }
    if ($null -eq $app) {
        [Console]::Out.WriteLine('COWORK_UNAVAILABLE')
        throw '没有可用的 Microsoft Office 或 WPS 自动化接口。请安装或修复办公软件，或用系统应用打开。'
    }
    $wps = $renderer -notin @('Word.Application', 'Excel.Application', 'PowerPoint.Application')
    [Console]::Out.WriteLine('COWORK_RENDERER:' + $renderer)
    $stage = 'prepare'
    [uint32]$officeProcessId = 0
    if ($wps) {
        # WPS can share a process across windows and document families. Do not
        # hide/quit the app or assign it to a kill-on-close job, even if new.
        [Console]::Out.WriteLine('COWORK_SHARED:0')
        $count = if ($cfg.family -eq 'word') { $app.Documents.Count } elseif ($cfg.family -eq 'excel') { $app.Workbooks.Count } else { $app.Presentations.Count }
        if ($count -gt 0) { throw 'WPS 当前有打开的文档，无法确认独立转换。请先保存并关闭 WPS 文档，或选择其他渲染方式。' }
    } elseif ($cfg.family -eq 'word') {
        $bootstrap = $app.Documents.Add()
        $windowHandle = $bootstrap.Windows.Item(1).Hwnd
    } else { $windowHandle = $app.Hwnd }
    if (-not $wps) {
        [void][CoworkWindowProcess]::GetWindowThreadProcessId([IntPtr][long]$windowHandle, [ref]$officeProcessId)
        $owned = ($officeProcessId -gt 0 -and $ids -notcontains [int]$officeProcessId)
        if ($cfg.family -eq 'powerpoint') {
            # Even a newly created PowerPoint server can later acquire user windows.
            # Never put this shared server in a kill-on-close process job.
            [Console]::Out.WriteLine('COWORK_SHARED:' + $officeProcessId)
        } else {
            if (-not $owned) { throw '无法创建独立的 Office 渲染实例，请稍后重试或使用系统应用打开。' }
            [Console]::Out.WriteLine('COWORK_PID:' + $officeProcessId)
        }
    }
    [Console]::Out.Flush()
    Assert-Active
    if ($null -ne $bootstrap) { $bootstrap.Close($false); [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($bootstrap); $bootstrap = $null }
    if ($wps) {
        # Shared application settings are never modified. Python has rejected
        # macros, embedded active content and remote dependencies before COM.
        $stage = 'open'
        if ($cfg.family -eq 'word') {
            $doc = $app.Documents.Open([string]$cfg.source, $false, $true, $false, '', '', $false, '', '', 0, 0, $false)
        } elseif ($cfg.family -eq 'excel') {
            $doc = $app.Workbooks.Open([string]$cfg.source, 0, $true, [Type]::Missing, '', '', $true)
        } else {
            $doc = $app.Presentations.Open([string]$cfg.source, -1, 0, 0)
        }
        Assert-Active
        if ($null -eq $doc) { throw 'WPS 未返回只读文档。' }
        $stage = 'export'
        if ($cfg.family -eq 'word') {
            $doc.ExportAsFixedFormat([string]$cfg.target, 17, $false, 0)
        } elseif ($cfg.family -eq 'excel') {
            Export-Workbook
        } else {
            $doc.SaveAs([string]$cfg.target, 32)
        }
    } elseif ($cfg.family -eq 'word') {
        $app.AutomationSecurity = 3
        $app.Visible = $false
        $app.DisplayAlerts = 0
        $app.Options.UpdateLinksAtOpen = $false
        $stage = 'open'
        $doc = $app.Documents.Open([string]$cfg.source, $false, $true, $false, '', '', $false, '', '', 0, 0, $false, $false, 0, $true)
        $stage = 'export'
        Assert-Active
        $doc.ExportAsFixedFormat([string]$cfg.target, 17, $false, 0)
    } elseif ($cfg.family -eq 'excel') {
        $app.AutomationSecurity = 3
        $app.Visible = $false
        $app.DisplayAlerts = $false
        $app.AskToUpdateLinks = $false
        $app.EnableEvents = $false
        $stage = 'open'
        $doc = $app.Workbooks.Open([string]$cfg.source, 0, $true, [Type]::Missing, '', '', $true, [Type]::Missing, [Type]::Missing, $false, $false, [Type]::Missing, $false)
        $app.Calculation = -4135
        $stage = 'export'
        Assert-Active
        Export-Workbook
    } else {
        $stage = 'open'
        $doc = $app.Presentations.Open([string]$cfg.source, -1, 0, 0)
        $stage = 'export'
        Assert-Active
        $doc.SaveAs([string]$cfg.target, 32)
    }
    Assert-Active
    [Console]::Out.WriteLine('COWORK_OK')
} catch {
    [Console]::Error.WriteLine($renderer + ' [' + $stage + ':' + $_.InvocationInfo.ScriptLineNumber + ']: ' + $_.Exception.Message)
    exit 1
} finally {
    if ($null -ne $bootstrap) {
        try { $bootstrap.Close($false) }
        catch { $clean = $false; [Console]::Error.WriteLine('Office cleanup: ' + $_.Exception.Message) }
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($bootstrap)
    }
    if ($null -ne $doc) {
        try {
            if ($cfg.family -eq 'powerpoint') { $doc.Close() } else { $doc.Close($false) }
        } catch { $clean = $false; [Console]::Error.WriteLine('Office cleanup: ' + $_.Exception.Message) }
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($doc)
    }
    if ($null -ne $app) {
        # Do not Quit or kill an application: it may have acquired user windows
        # after activation. Release our references after closing our copy only.
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($app)
    }
    if ($stateOwned -and $clean) { Remove-Item -LiteralPath $cfg.state -Force -ErrorAction SilentlyContinue }
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
    # Also clean up if the desktop has already stopped waiting for a shared COM
    # call. The helper must reach finally; terminating it would skip Doc.Close.
    # Activation-only failure may be followed by a 32-bit helper using this
    # snapshot. Python owns cleanup if no provider could be activated.
    if ($null -ne $app) { Remove-Item -LiteralPath $cfg.source -Force -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $cfg.cancel) {
        Remove-Item -LiteralPath $cfg.target,$cfg.cancel -Force -ErrorAction SilentlyContinue
    }
}
'''


def _finish_office_helper(process, paths):
    """Reap a cooperatively cancelled helper without killing shared Office."""
    try:
        process.wait()
        if process.stdout:
            process.stdout.close()
        log.info("office_preview.cleanup_finish helper_pid=%s", process.pid)
    finally:
        for part in paths[0].parent.glob(paths[0].name + '.part*.pdf'):
            part.unlink(missing_ok=True)
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                log.exception("office_preview.cleanup_error helper_pid=%s", process.pid)


def _excel_options(source, value, data):
    import re
    if value is None: return None
    if not isinstance(value, dict) or set(value)-{'mode','sheet','range','identity_columns','header_rows'}:
        raise ValueError('无效的 Excel 阅读选项。')
    options = {key:str(value.get(key) or '') for key in ('mode','sheet','range','identity_columns','header_rows')}
    if options['mode'] not in {'region','print'}: raise ValueError('请选择区域阅读或打印预览。')
    if options['mode']=='print': return None
    if source.suffix.lower()!='.xlsx': raise ValueError('区域阅读需要 XLSX；旧格式请用表格视图或打印预览。')
    if options['range'] and not re.fullmatch(r'[A-Z]{1,3}[1-9][0-9]*:[A-Z]{1,3}[1-9][0-9]*', options['range']):
        raise ValueError('阅读范围应为 A1:R80 这样的单一区域。')
    if options['identity_columns'] and not re.fullmatch(r'[A-Z]{1,3}:[A-Z]{1,3}',options['identity_columns']):
        raise ValueError('重复标识列应为 A:B 这样的连续列范围。')
    if options['header_rows'] and not re.fullmatch(r'[1-9][0-9]*:[1-9][0-9]*',options['header_rows']):
        raise ValueError('重复标题行应为 1:2 这样的连续行范围。')
    import io
    from openpyxl import load_workbook
    workbook = load_workbook(io.BytesIO(data), data_only=False)
    try:
        options['merges']=sorted([{'sheet':sheet.title,'first':area.min_col,'last':area.max_col}
            for sheet in workbook for area in sheet.merged_cells.ranges if area.min_col!=area.max_col],key=lambda area:(area['sheet'],area['first']))
    finally: workbook.close()
    return options


def render_office_pdf(path, *, cancel=None, timeout=120, root=None, force=False, renderer="auto", excel_options=None, source_data=None):
    from .office_renderers import select_renderer, state_path, state_root
    source = Path(path).resolve()
    family = OFFICE_FAMILIES.get(source.suffix.lower())
    if family is None:
        raise WorkbenchError("unsupported", "此格式没有 Office 版式预览。")
    selected = select_renderer(family, renderer)
    data = source_bytes(source) if source_data is None else source_data
    options = _excel_options(source, excel_options, data) if family == 'excel' else None
    if selected['id'] == 'libreoffice':
        if options and options['mode']=='region':
            raise WorkbenchError('region_unavailable', 'LibreOffice 暂不提供区域分页。可使用表格视图，或明确选择打印预览。')
        return _render_libreoffice_pdf(
            source, data, cancel=cancel, timeout=timeout, root=root, force=force,
            require_current=source_data is None,
        )
    if selected['id'] == 'wps' or family == 'powerpoint':
        _check_shared_office_source(source, data)
    fingerprint = hashlib.sha256(data).hexdigest()
    root = Path(root or cache_root())
    root.mkdir(parents=True, exist_ok=True)
    option_key = hashlib.sha256(json.dumps(options,sort_keys=True).encode()).hexdigest()[:12]
    target = root / ("office-v3-" + selected['version'][:20] + '-' + option_key + '-' + fingerprint + ".pdf")
    if target.is_file() and not force:
        return str(target), fingerprint
    cancel = cancel or threading.Event()
    if cancel.is_set():
        raise WorkbenchError("cancelled", "已取消版式预览，源文件未修改。")
    partial = root / (uuid.uuid4().hex + ".pdf")
    # Read a versioned copy, so external writes cannot change the conversion halfway.
    snapshot = root / (uuid.uuid4().hex + source.suffix)
    snapshot.write_bytes(data)
    cancel_path = root / (uuid.uuid4().hex + ".cancel")
    state = state_path(selected['group'])
    payload = base64.b64encode(json.dumps({"family": family, "group": selected['group'], "state": str(state),
        "renderers": [selected['progid']], "source": str(snapshot), "target": str(partial), "cancel": str(cancel_path), 'excel':options}).encode("utf-8")).decode("ascii")
    script = _OFFICE_SCRIPT.replace("__PAYLOAD__", payload)
    script_path = root / (uuid.uuid4().hex + '.ps1')
    script_path.write_text(script, encoding='utf-8-sig')
    events = queue.Queue()
    process = None
    errors = []
    renderer = ""
    log.info("office_preview.start family=%s source_version=%s", family, fingerprint[:12])
    try:
        def read_lines(helper):
            for raw in iter(helper.stdout.readline, b""):
                events.put(raw.decode("utf-8", errors="replace").strip())
            events.put(None)
        def start_helper(executable):
            log.info("office_preview.helper family=%s host=%s source_version=%s", family, executable, fingerprint[:12])
            helper = subprocess.Popen([executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-STA", "-ExecutionPolicy", "Bypass", "-File", str(script_path)],
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      **subprocess_kwargs_no_window())
            threading.Thread(target=read_lines, args=(helper,), daemon=True).start()
            return helper
        process = start_helper(selected['host'])
        deadline = time.monotonic() + timeout
        complete = False
        unavailable = False
        while True:
            if cancel.is_set():
                # Let COM publish its owned PID before tearing down the helper.
                # Killing the helper during activation can orphan its Office server.
                cancel_path.touch(exist_ok=True)
                raise WorkbenchError("cancelled", "已停止等待预览，原文件未修改。本机转换可能仍在收尾，可重新检测可用性。")
            if time.monotonic() > deadline:
                raise WorkbenchError("renderer_timeout", "本机办公软件无响应，可能存在待处理弹窗，请切到对应窗口查看。已停止等待，原文件与已有预览保留；旧任务退出前不启动新转换。")
            try:
                line = events.get(timeout=0.1)
            except queue.Empty:
                continue
            if line is None:
                break
            if line == "COWORK_UNAVAILABLE":
                unavailable = True
            elif line.startswith("COWORK_RENDERER:"):
                renderer = line.partition(":")[2]
                errors.clear()
                log.info("office_preview.renderer family=%s renderer=%s source_version=%s", family, renderer, fingerprint[:12])
            elif line.startswith("COWORK_PROBE:"):
                detail = line.partition(":")[2]
                log.info("office_preview.probe_failed family=%s detail=%s", family, detail)
                errors.append(detail)
            elif line.startswith("COWORK_PID:"):
                log.info("office_preview.instance family=%s office_pid=%s", family, line.partition(":")[2])
            elif line.startswith("COWORK_SHARED:"):
                log.info("office_preview.shared family=%s office_pid=%s", family, line.partition(":")[2])
            elif line == "COWORK_OK":
                complete = True
            elif line:
                errors.append(line)
        if cancel.is_set():
            raise WorkbenchError("cancelled", "已取消版式预览，源文件未修改。")
        exit_code = process.wait(timeout=2)
        if unavailable:
            raise WorkbenchError("renderer_unavailable", "所选办公软件的自动化接口不可用。请修复软件后重试，或明确选择其他渲染方式，也可用本机应用打开。")
        parts = sorted(root.glob(partial.name + '.part*.pdf'),key=lambda item:int(item.name.rsplit('.part',1)[1].split('.')[0]))
        if exit_code == 0 and complete and parts:
            from pypdf import PdfWriter
            writer = PdfWriter()
            try:
                for part in parts: writer.append(str(part))
                with partial.open('wb') as handle: writer.write(handle)
            finally: writer.close()
        if exit_code != 0 or not complete or not partial.is_file():
            raise WorkbenchError("renderer_failed", "无法生成版式预览（Microsoft Office / WPS）。" + "\n" + "\n".join(errors[-8:]))
        if state.exists():
            raise WorkbenchError('renderer_cleanup_pending', '页面已转换，但未确认本机文档清理完成；已有预览保留。请关闭对应软件后重新检测。')
        if not partial.read_bytes().startswith(b"%PDF-"):
            raise WorkbenchError("invalid_pdf", "办公软件没有返回有效的 PDF，请重试或用系统应用打开。")
        if cancel.is_set():
            raise WorkbenchError("cancelled", "已取消版式预览，源文件未修改。")
        if source_data is None and hashlib.sha256(source_bytes(source)).hexdigest() != fingerprint:
            raise WorkbenchError("source_changed", "文件在转换时发生修改，请重新预览最新版本。")
        os.replace(partial, target)
        # This confirms this version/family actually exported and closed a copy;
        # it does not promise arbitrary future files or fonts will succeed.
        evidence = state_root() / (selected['version'] + '-' + family + '.verified.json')
        evidence.write_text(json.dumps({'renderer': selected['progid'], 'bits': selected['bits'],
            'version': selected['version'], 'family': family, 'verified_at': time.time()}), encoding='utf-8')
        log.info("office_preview.finish family=%s renderer=%s source_version=%s", family, renderer, fingerprint[:12])
        return str(target), fingerprint
    except Exception as exc:
        if isinstance(exc, WorkbenchError) and exc.code == "cancelled":
            log.info("office_preview.cancel family=%s source_version=%s", family, fingerprint[:12])
        else:
            log.exception("office_preview.error family=%s source_version=%s", family, fingerprint[:12])
        raise
    finally:
        if process is not None and process.poll() is None:
            cancel_path.touch(exist_ok=True)
            log.info("office_preview.cleanup_pending family=%s helper_pid=%s", family, process.pid)
            # Keep COM alive until its current call completes and closes only
            # this request's hidden document. A running user app is never killed.
            threading.Thread(target=_finish_office_helper,
                             args=(process, (partial, snapshot, cancel_path, script_path)), daemon=True).start()
            process = None
            deferred_cleanup = True
        else:
            deferred_cleanup = False
        if process is not None:
            process.wait(timeout=5)
            if process.stdout:
                process.stdout.close()
        if not deferred_cleanup:
            for part in root.glob(partial.name + '.part*.pdf'): part.unlink(missing_ok=True)
            script_path.unlink(missing_ok=True)
            partial.unlink(missing_ok=True)
            snapshot.unlink(missing_ok=True)
            cancel_path.unlink(missing_ok=True)


def _check_shared_office_source(source, data):
    """Shared COM applications keep user settings. Only passive OOXML is opened."""
    import io
    from xml.etree import ElementTree
    if source.suffix.lower() not in {'.docx', '.xlsx', '.pptx'}:
        raise WorkbenchError('renderer_unsafe_shared', '此格式无法在共享的办公软件实例中安全转换。请用本机应用另存为 DOCX、XLSX 或 PPTX，或选择 LibreOffice。')
    def check_package(content, depth=0):
        if depth > 2:
            raise WorkbenchError('renderer_unsafe_shared', '文件包含多层嵌入文档，请用本机应用打开。')
        with zipfile.ZipFile(io.BytesIO(content)) as package:
            if sum(entry.file_size for entry in package.infolist()) > 256 * 1024 * 1024:
                raise ValueError('展开后的文档过大')
            for entry in package.infolist():
                name = entry.filename.lower()
                if any(part in name for part in ('vbaproject', '/activex/', '/externallinks/')):
                    raise WorkbenchError('renderer_unsafe_shared', '文件包含宏、活动对象或外部依赖，请用本机应用打开；未修改原文件。')
                if '/embeddings/' in name and not entry.is_dir():
                    # PowerPoint charts normally embed a passive XLSX data book.
                    # Verify its contents, rather than treating every chart as OLE.
                    if not name.endswith('.xlsx') or entry.file_size > MAX_DOCUMENT_BYTES:
                        raise WorkbenchError('renderer_unsafe_shared', '文件包含需要原软件处理的嵌入对象，请用本机应用打开。')
                    check_package(package.read(entry), depth+1)
                if name == '[content_types].xml' and b'macroenabled' in package.read(entry).lower():
                    raise WorkbenchError('renderer_unsafe_shared', '文件包含宏，请用本机应用打开。')
                if name.endswith('.rels'):
                    if entry.file_size > 2 * 1024 * 1024:
                        raise ValueError('文档关系文件过大')
                    for relationship in ElementTree.fromstring(package.read(entry)):
                        if relationship.get('TargetMode') == 'External' and not relationship.get('Type', '').endswith('/hyperlink'):
                            raise WorkbenchError('renderer_unsafe_shared', '文件引用了外部资源，请用本机应用打开或选择其他渲染方式；未修改原文件。')
    try:
        check_package(data)
    except (zipfile.BadZipFile, ElementTree.ParseError, ValueError) as exc:
        raise WorkbenchError('renderer_failed', '文件不是有效的 Office 文档，或已加密；请用本机应用检查后重试。') from exc


def _libreoffice_executable():
    from .libreoffice_component import managed_executable
    candidates = [os.environ.get("COWORK_LIBREOFFICE_EXE"), managed_executable(), shutil.which("soffice"),
                  shutil.which("libreoffice"), "/Applications/LibreOffice.app/Contents/MacOS/soffice"]
    if os.name == "nt":
        candidates.extend(str(Path(folder) / "LibreOffice/program/soffice.com")
                          for folder in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")) if folder)
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())
    raise WorkbenchError("renderer_unavailable", "需要安装 LibreOffice 才能预览页面版式。也可以用本机应用打开原文件。")


@contextmanager
def _libreoffice_staging(root):
    from .platform_runtime import platform_runtime
    root = Path(root).resolve()
    staging = Path(tempfile.mkdtemp(prefix="libreoffice-", dir=root)).resolve()
    if not staging.is_relative_to(root):
        raise ValueError("文档预览临时目录无效。")
    try:
        yield staging
    finally:
        # Keep normal paths for soffice/file URIs, extended paths only for I/O:
        # LibreOffice creates deeply nested extension caches on Windows.
        shutil.rmtree(platform_runtime.filesystem_path(staging))


def _render_libreoffice_pdf(source, data, *, cancel=None, timeout=120, root=None, force=False, require_current):
    from .process_utils import popen_external_program, terminate_process_tree
    cancel = cancel or threading.Event()
    if cancel.is_set():
        raise WorkbenchError("cancelled", "已取消版式预览，源文件未修改。")
    executable = _libreoffice_executable()
    fingerprint = hashlib.sha256(data).hexdigest()
    binary = os.stat(executable)
    renderer_key = hashlib.sha256(f"{executable}|{binary.st_size}|{binary.st_mtime_ns}".encode()).hexdigest()[:16]
    root = Path(root or cache_root())
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"libreoffice-v1-{renderer_key}-{fingerprint}.pdf"
    if target.is_file() and not force:
        return str(target), fingerprint
    log.info("office_preview.start renderer=libreoffice source_version=%s", fingerprint[:12])
    process = None
    try:
        with _libreoffice_staging(root) as staging:
            snapshot = staging / ("source" + source.suffix)
            snapshot.write_bytes(data)
            profile = staging / "profile"
            output = staging / "source.pdf"
            with (staging / "conversion.log").open("wb") as trace:
                process = popen_external_program(
                    [executable, "-env:UserInstallation=" + profile.as_uri(), "--headless", "--nologo",
                     "--nodefault", "--norestore", "--convert-to", "pdf", "--outdir", str(staging), str(snapshot)],
                    stdin=subprocess.DEVNULL, stdout=trace, stderr=subprocess.STDOUT,
                    start_new_session=os.name != "nt", **subprocess_kwargs_no_window(),
                )
                deadline = time.monotonic() + timeout
                try:
                    while process.poll() is None:
                        if cancel.wait(.05):
                            raise WorkbenchError("cancelled", "已取消版式预览，源文件未修改。")
                        if time.monotonic() >= deadline:
                            raise WorkbenchError("renderer_timeout", "LibreOffice 版式预览超时，请检查文件后重试，或用系统应用打开。")
                finally:
                    if process.poll() is None:
                        terminate_process_tree(process)
                if process.returncode != 0 or not output.is_file():
                    raise WorkbenchError("renderer_failed", "LibreOffice 未能转换此文档。源文件未修改，请重试或用本机应用打开。")
            from pypdf import PdfReader
            try:
                if not PdfReader(str(output)).pages:
                    raise ValueError("empty PDF")
            except Exception as exc:
                raise WorkbenchError("invalid_pdf", "LibreOffice 没有返回有效的 PDF，请重试或用系统应用打开。") from exc
            if cancel.is_set():
                raise WorkbenchError("cancelled", "已取消版式预览，源文件未修改。")
            if require_current and hashlib.sha256(source_bytes(source)).hexdigest() != fingerprint:
                raise WorkbenchError("source_changed", "文件在转换时发生修改，请重新预览最新版本。")
            os.replace(output, target)
        log.info("office_preview.finish renderer=libreoffice source_version=%s", fingerprint[:12])
        return str(target), fingerprint
    except Exception as exc:
        log.warning("office_preview.failed renderer=libreoffice source_version=%s reason=%s exit=%s",
                    fingerprint[:12], getattr(exc, "code", type(exc).__name__), getattr(process, "returncode", None))
        raise
