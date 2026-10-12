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
OFFICE_RENDERERS = {
    "word": ("Word.Application", "KWPS.Application", "WPS.Application"),
    "excel": ("Excel.Application", "KET.Application", "ET.Application"),
    "powerpoint": ("PowerPoint.Application", "KWPP.Application", "WPP.Application"),
}


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
$app = $null; $doc = $null; $bootstrap = $null; $owned = $false
$wps = $false; $renderer = ''; $stage = 'activate'
$mutex = New-Object System.Threading.Mutex($false, ('Local\CoworkOfficePreview-' + $cfg.family))
$locked = $false
function Assert-Active {
    if (Test-Path -LiteralPath $cfg.cancel) { throw '已取消版式预览，源文件未修改。' }
}
try {
    # PowerPoint is MultiUse: serialize our helpers without taking ownership of
    # an existing application or interfering with the user's presentations.
    while (-not $locked) {
        Assert-Active
        try { $locked = $mutex.WaitOne(100) }
        catch [System.Threading.AbandonedMutexException] { $locked = $true }
    }
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
    if ($null -ne $bootstrap) { $bootstrap.Close($false); $bootstrap = $null }
    if ($wps) {
        # Restore shared application settings even when Open throws. Only this
        # request's read-only snapshot is closed in finally.
        $security = $app.AutomationSecurity
        $stage = 'open'
        try {
            $app.AutomationSecurity = 3
            if ($cfg.family -eq 'word') {
                $links = $app.Options.UpdateLinksAtOpen
                try {
                    $app.Options.UpdateLinksAtOpen = $false
                    $doc = $app.Documents.Open([string]$cfg.source, $false, $true, $false, '', '', $false, '', '', 0, 0, $false)
                } finally { $app.Options.UpdateLinksAtOpen = $links }
            } elseif ($cfg.family -eq 'excel') {
                $events = $app.EnableEvents
                try {
                    $app.EnableEvents = $false
                    $doc = $app.Workbooks.Open([string]$cfg.source, 0, $true, [Type]::Missing, '', '', $true)
                } finally { $app.EnableEvents = $events }
            } else {
                $doc = $app.Presentations.Open([string]$cfg.source, -1, 0, 0)
            }
        } finally { $app.AutomationSecurity = $security }
        Assert-Active
        if ($null -eq $doc) { throw 'WPS 未返回只读文档。' }
        $stage = 'export'
        if ($cfg.family -eq 'word') {
            $doc.ExportAsFixedFormat([string]$cfg.target, 17, $false, 0)
        } elseif ($cfg.family -eq 'excel') {
            $doc.ExportAsFixedFormat(0, [string]$cfg.target, 0, $false, $false)
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
        $doc.ExportAsFixedFormat(0, [string]$cfg.target, 0, $false, $false)
    } else {
        $security = $app.AutomationSecurity
        $stage = 'open'
        try {
            $app.AutomationSecurity = 3
            $doc = $app.Presentations.Open([string]$cfg.source, -1, 0, 0)
        } finally { $app.AutomationSecurity = $security }
        $stage = 'export'
        Assert-Active
        $doc.SaveAs([string]$cfg.target, 32)
    }
    Assert-Active
    [Console]::Out.WriteLine('COWORK_OK')
} catch {
    [Console]::Error.WriteLine($renderer + ' [' + $stage + ']: ' + $_.Exception.Message)
    exit 1
} finally {
    if ($null -ne $bootstrap) {
        try { $bootstrap.Close($false) }
        catch { [Console]::Error.WriteLine('Office cleanup: ' + $_.Exception.Message) }
    }
    if ($null -ne $doc) {
        try {
            if ($cfg.family -eq 'powerpoint') { $doc.Close() } else { $doc.Close($false) }
        } catch { [Console]::Error.WriteLine('Office cleanup: ' + $_.Exception.Message) }
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($doc)
    }
    if ($null -ne $app) {
        try {
            if ($owned -and ($cfg.family -ne 'powerpoint' -or $app.Presentations.Count -eq 0)) { $app.Quit() }
        } catch { [Console]::Error.WriteLine('Office cleanup: ' + $_.Exception.Message) }
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($app)
    }
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


def _office_powershell_executables():
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    hosts = [windows / "System32/WindowsPowerShell/v1.0/powershell.exe"]
    # Some WPS installations register their COM server only in the 32-bit
    # registry view. Try that host only after *all* activations fail, before
    # any document is opened; never repeat an open/export in another process.
    x86 = windows / "SysWOW64/WindowsPowerShell/v1.0/powershell.exe"
    if x86.is_file():
        hosts.append(x86)
    return [str(path) for path in hosts]


def _finish_office_helper(process, paths):
    """Reap a cooperatively cancelled helper without killing shared Office."""
    try:
        process.wait()
        if process.stdout:
            process.stdout.close()
        log.info("office_preview.cleanup_finish helper_pid=%s", process.pid)
    finally:
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                log.exception("office_preview.cleanup_error helper_pid=%s", process.pid)


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
    cancel_path = root / (uuid.uuid4().hex + ".cancel")
    payload = base64.b64encode(json.dumps({"family": family, "renderers": OFFICE_RENDERERS[family], "source": str(snapshot), "target": str(partial), "cancel": str(cancel_path)}).encode("utf-8")).decode("ascii")
    script = _OFFICE_SCRIPT.replace("__PAYLOAD__", payload)
    executables = iter(_office_powershell_executables())
    events = queue.Queue()
    process = None
    owned_job = None
    shared = False
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
            helper = subprocess.Popen([executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-STA", "-EncodedCommand",
                                       base64.b64encode(script.encode("utf-16-le")).decode("ascii")],
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      **subprocess_kwargs_no_window())
            threading.Thread(target=read_lines, args=(helper,), daemon=True).start()
            return helper
        process = start_helper(next(executables))
        deadline = time.monotonic() + timeout
        complete = False
        unavailable = False
        cancellation_deadline = None
        while True:
            if cancel.is_set():
                # Let COM publish its owned PID before tearing down the helper.
                # Killing the helper during activation can orphan its Office server.
                cancellation_deadline = cancellation_deadline or (time.monotonic() + 10)
                cancel_path.touch(exist_ok=True)
                if owned_job or shared or time.monotonic() >= cancellation_deadline:
                    raise WorkbenchError("cancelled", "已取消版式预览，源文件未修改。")
            if time.monotonic() > deadline:
                raise WorkbenchError("renderer_timeout", "版式预览超时，请检查文件后重试，或用系统应用打开。")
            try:
                line = events.get(timeout=0.1)
            except queue.Empty:
                continue
            if line is None:
                executable = next(executables, None) if unavailable else None
                if executable and not cancel.is_set():
                    process.wait(timeout=2)
                    process.stdout.close()
                    unavailable = False
                    process = start_helper(executable)
                    continue
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
                from .workbench_terminal import WindowsProcessJob
                owned_job = WindowsProcessJob(int(line.partition(":")[2]))
            elif line.startswith("COWORK_SHARED:"):
                shared = True
                log.info("office_preview.shared family=%s office_pid=%s", family, line.partition(":")[2])
            elif line == "COWORK_OK":
                complete = True
            elif line:
                errors.append(line)
        if cancel.is_set():
            raise WorkbenchError("cancelled", "已取消版式预览，源文件未修改。")
        exit_code = process.wait(timeout=2)
        if unavailable:
            raise WorkbenchError("renderer_unavailable", "未找到可用的 Microsoft Office 或 WPS 自动化接口。\n请安装或修复办公软件后重试，也可以选择简化预览或用系统应用打开。")
        if exit_code != 0 or not complete or not partial.is_file():
            raise WorkbenchError("renderer_failed", "无法生成版式预览（Microsoft Office / WPS）。" + "\n" + "\n".join(errors[-8:]))
        if not partial.read_bytes().startswith(b"%PDF-"):
            raise WorkbenchError("invalid_pdf", "办公软件没有返回有效的 PDF，请重试或用系统应用打开。")
        if cancel.is_set():
            raise WorkbenchError("cancelled", "已取消版式预览，源文件未修改。")
        if hashlib.sha256(source_bytes(source)).hexdigest() != fingerprint:
            raise WorkbenchError("source_changed", "文件在转换时发生修改，请重新预览最新版本。")
        os.replace(partial, target)
        log.info("office_preview.finish family=%s renderer=%s source_version=%s", family, renderer, fingerprint[:12])
        return str(target), fingerprint
    except Exception as exc:
        if isinstance(exc, WorkbenchError) and exc.code == "cancelled":
            log.info("office_preview.cancel family=%s source_version=%s", family, fingerprint[:12])
        else:
            log.exception("office_preview.error family=%s source_version=%s", family, fingerprint[:12])
        raise
    finally:
        if process is not None and process.poll() is None and not owned_job:
            cancel_path.touch(exist_ok=True)
            log.info("office_preview.cleanup_pending family=%s helper_pid=%s", family, process.pid)
            # Keep COM alive until its current call completes and closes only
            # this request's hidden document. A running user app is never killed.
            threading.Thread(target=_finish_office_helper,
                             args=(process, (partial, snapshot, cancel_path)), daemon=True).start()
            process = None
            deferred_cleanup = True
        else:
            deferred_cleanup = False
        if owned_job:
            owned_job.close()
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            if process.stdout:
                process.stdout.close()
        if not deferred_cleanup:
            partial.unlink(missing_ok=True)
            snapshot.unlink(missing_ok=True)
            cancel_path.unlink(missing_ok=True)
