"""Versioned PDF copies and read-only Office rendering, outside the UI thread."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid

from .env_utils import get_app_data_dir
from .process_utils import subprocess_kwargs_no_window
from .workbench import WorkbenchError

log = logging.getLogger(__name__)
OFFICE_FAMILIES = {".doc": "word", ".docx": "word", ".ppt": "powerpoint",
                   ".pptx": "powerpoint", ".xls": "excel", ".xlsx": "excel"}
MAX_DOCUMENT_BYTES = 100 * 1024 * 1024


def cache_root():
    path = Path(get_app_data_dir()) / "browser_previews"
    path.mkdir(parents=True, exist_ok=True)
    return path


def source_bytes(path):
    if os.path.getsize(path) > MAX_DOCUMENT_BYTES:
        raise WorkbenchError("document_too_large", "文件超过 100 MiB，请使用系统应用打开。")
    with open(path, "rb") as stream:
        data = stream.read(MAX_DOCUMENT_BYTES + 1)
    if len(data) > MAX_DOCUMENT_BYTES:
        raise WorkbenchError("document_too_large", "文件超过 100 MiB，请使用系统应用打开。")
    return data


def cache_pdf(path, root=None):
    data = source_bytes(path)
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


_OFFICE_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$cfg = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__PAYLOAD__')) | ConvertFrom-Json
Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class CoworkWindowProcess { [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId); }'
$names = @{word='WINWORD'; excel='EXCEL'; powerpoint='POWERPNT'}
$ids = @(Get-Process -Name $names[$cfg.family] -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
$app = $null; $doc = $null; $bootstrap = $null; $owned = $false
try {
    $progIds = @{word='Word.Application'; excel='Excel.Application'; powerpoint='PowerPoint.Application'}
    $type = [Type]::GetTypeFromProgID($progIds[$cfg.family], $false)
    if ($null -eq $type) { throw '本机没有可用的 Microsoft Office 渲染器。' }
    $app = [Activator]::CreateInstance($type)
    [uint32]$officeProcessId = 0
    if ($cfg.family -eq 'word') {
        $bootstrap = $app.Documents.Add()
        $windowHandle = $bootstrap.Windows.Item(1).Hwnd
    } else { $windowHandle = $app.Hwnd }
    [void][CoworkWindowProcess]::GetWindowThreadProcessId([IntPtr][long]$windowHandle, [ref]$officeProcessId)
    $owned = ($officeProcessId -gt 0 -and $ids -notcontains [int]$officeProcessId)
    if (-not $owned) { throw 'Office 当前复用了已有进程。为保留正在编辑的文档，请关闭该 Office 应用后重试，或使用系统应用打开。' }
    [Console]::Out.WriteLine('COWORK_PID:' + $officeProcessId)
    [Console]::Out.Flush()
    if ($null -ne $bootstrap) { $bootstrap.Close($false); $bootstrap = $null }
    $app.AutomationSecurity = 3
    if ($cfg.family -eq 'word') {
        $app.Visible = $false
        $app.DisplayAlerts = 0
        $app.Options.UpdateLinksAtOpen = $false
        $doc = $app.Documents.Open([string]$cfg.source, $false, $true, $false, '', '', $false, '', '', 0, 0, $false, $false, 0, $true)
        $doc.ExportAsFixedFormat([string]$cfg.target, 17, $false, 0)
    } elseif ($cfg.family -eq 'excel') {
        $app.Visible = $false
        $app.DisplayAlerts = $false
        $app.AskToUpdateLinks = $false
        $app.EnableEvents = $false
        $doc = $app.Workbooks.Open([string]$cfg.source, 0, $true, [Type]::Missing, '', '', $true, [Type]::Missing, [Type]::Missing, $false, $false, [Type]::Missing, $false)
        $app.Calculation = -4135
        $doc.ExportAsFixedFormat(0, [string]$cfg.target, 0, $false, $false)
    } else {
        $app.DisplayAlerts = 1
        $doc = $app.Presentations.Open([string]$cfg.source, -1, 0, 0)
        $doc.SaveAs([string]$cfg.target, 32)
    }
    [Console]::Out.WriteLine('COWORK_OK')
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
} finally {
    if ($null -ne $bootstrap) { $bootstrap.Close($false) }
    if ($null -ne $doc) {
        if ($cfg.family -eq 'powerpoint') { $doc.Close() } else { $doc.Close($false) }
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($doc)
    }
    if ($null -ne $app) {
        if ($owned) { $app.Quit() }
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($app)
    }
}
'''


def render_office_pdf(path, *, cancel=None, timeout=120, root=None, force=False):
    source = Path(path).resolve()
    family = OFFICE_FAMILIES.get(source.suffix.lower())
    if family is None:
        raise WorkbenchError("unsupported", "此格式没有 Office 版式预览。")
    data = source_bytes(source)
    fingerprint = hashlib.sha256(data).hexdigest()
    root = Path(root or cache_root())
    root.mkdir(parents=True, exist_ok=True)
    target = root / ("office-v1-" + fingerprint + ".pdf")
    if target.is_file() and not force:
        return str(target), fingerprint
    if os.name != "nt":
        raise WorkbenchError("renderer_unavailable", "本机 Office 版式预览需要 Windows。")
    cancel = cancel or threading.Event()
    if cancel.is_set():
        raise WorkbenchError("cancelled", "已取消版式预览，源文件未修改。")
    partial = root / (uuid.uuid4().hex + ".pdf")
    # Read a versioned copy, so external writes cannot change the conversion halfway.
    snapshot = root / (uuid.uuid4().hex + source.suffix)
    snapshot.write_bytes(data)
    payload = base64.b64encode(json.dumps({"family": family, "source": str(snapshot), "target": str(partial)}).encode("utf-8")).decode("ascii")
    script = _OFFICE_SCRIPT.replace("__PAYLOAD__", payload)
    executable = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    events = queue.Queue()
    process = None
    owned_job = None
    errors = []
    log.info("office_preview.start family=%s source_version=%s", family, fingerprint[:12])
    try:
        process = subprocess.Popen([executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-STA", "-EncodedCommand",
                                    base64.b64encode(script.encode("utf-16-le")).decode("ascii")],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   **subprocess_kwargs_no_window())
        def read_lines():
            for raw in iter(process.stdout.readline, b""):
                events.put(raw.decode("utf-8", errors="replace").strip())
            events.put(None)
        reader = threading.Thread(target=read_lines, daemon=True)
        reader.start()
        deadline = time.monotonic() + timeout
        complete = False
        cancellation_deadline = None
        while True:
            if cancel.is_set():
                # Let COM publish its owned PID before tearing down the helper.
                # Killing the helper during activation can orphan its Office server.
                cancellation_deadline = cancellation_deadline or (time.monotonic() + 10)
                if owned_job or time.monotonic() >= cancellation_deadline:
                    raise WorkbenchError("cancelled", "已取消版式预览，源文件未修改。")
            if time.monotonic() > deadline:
                raise WorkbenchError("renderer_timeout", "Office 预览超时，请检查文件后重试。")
            try:
                line = events.get(timeout=0.1)
            except queue.Empty:
                continue
            if line is None:
                break
            if line.startswith("COWORK_PID:"):
                from .workbench_terminal import WindowsProcessJob
                owned_job = WindowsProcessJob(int(line.partition(":")[2]))
            elif line == "COWORK_OK":
                complete = True
            elif line:
                errors.append(line)
        if cancel.is_set():
            raise WorkbenchError("cancelled", "已取消版式预览，源文件未修改。")
        if process.wait(timeout=2) != 0 or not complete or not partial.is_file():
            raise WorkbenchError("renderer_failed", "Office 无法生成版式预览。" + "\n" + "\n".join(errors[-8:]))
        if not partial.read_bytes().startswith(b"%PDF-"):
            raise WorkbenchError("invalid_pdf", "Office 没有返回有效的 PDF。")
        if cancel.is_set():
            raise WorkbenchError("cancelled", "已取消版式预览，源文件未修改。")
        if hashlib.sha256(source_bytes(source)).hexdigest() != fingerprint:
            raise WorkbenchError("source_changed", "文件在转换时发生修改，请重新预览最新版本。")
        os.replace(partial, target)
        log.info("office_preview.finish family=%s source_version=%s", family, fingerprint[:12])
        return str(target), fingerprint
    except Exception:
        log.exception("office_preview.error family=%s source_version=%s", family, fingerprint[:12])
        raise
    finally:
        if owned_job:
            owned_job.close()
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            if process.stdout:
                process.stdout.close()
        partial.unlink(missing_ok=True)
        snapshot.unlink(missing_ok=True)
