"""Local presentation renderer discovery and slide image export.

The application bundles only these adapters. Microsoft PowerPoint or WPS
Presentation must already be installed and expose a compatible Automation
object on the current Windows machine.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import uuid

from .env_utils import get_app_data_dir
from .process_utils import subprocess_kwargs_no_window


RENDERER_POWERPOINT = "powerpoint"
RENDERER_WPS = "wps"
RENDERER_NONE = "none"

_RENDERER_PROG_IDS = {
    RENDERER_POWERPOINT: ("PowerPoint.Application",),
    # WPS has used both identifiers across desktop releases. Availability is
    # decided by an actual Automation probe rather than installation paths.
    RENDERER_WPS: ("KWPP.Application", "WPP.Application"),
}


def python_pptx_available():
    return importlib.util.find_spec("pptx") is not None


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def presentation_run_dir(run_id=None, create=False):
    safe_run_id = re.sub(r"[^A-Za-z0-9._-]+", "-", str(run_id or uuid.uuid4().hex)).strip("-.")
    if not safe_run_id:
        raise ValueError("PPT Agent run id 不能为空。")
    path = os.path.join(get_app_data_dir(), "ppt_agent_runs", safe_run_id)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def _powershell_executable():
    executable = shutil.which("powershell.exe") or shutil.which("powershell")
    if not executable:
        raise RuntimeError("当前系统未找到 Windows PowerShell，无法调用演示渲染器。")
    return executable


def _encoded_powershell(script):
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _run_powershell(script, timeout):
    completed = subprocess.run(
        [
            _powershell_executable(),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            _encoded_powershell(script),
        ],
        capture_output=True,
        text=True,
        timeout=max(1, int(timeout or 1)),
        check=False,
        **subprocess_kwargs_no_window(),
    )
    stdout = str(completed.stdout or "").strip()
    stderr = str(completed.stderr or "").strip()
    if completed.returncode != 0:
        detail = stderr or stdout or f"PowerShell exit code {completed.returncode}"
        raise RuntimeError(detail)
    return stdout


def _probe_prog_id(prog_id, timeout=12):
    payload = base64.b64encode(
        json.dumps({"prog_id": prog_id}, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    script = f"""
$ErrorActionPreference = 'Stop'
$cfg = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{payload}')) | ConvertFrom-Json
$app = $null
try {{
    $type = [Type]::GetTypeFromProgID([string]$cfg.prog_id, $false)
    if ($null -eq $type) {{ throw "Automation ProgID 未注册: $($cfg.prog_id)" }}
    $app = [Activator]::CreateInstance($type)
    if ($null -eq $app) {{ throw "Automation 对象创建失败: $($cfg.prog_id)" }}
    [Console]::Out.WriteLine('OK')
}} finally {{
    if ($null -ne $app) {{ try {{ $app.Quit() }} catch {{}} }}
}}
"""
    _run_powershell(script, timeout)
    return True


def detect_presentation_renderers(timeout_per_candidate=12):
    errors = {}
    available = []
    for renderer in (RENDERER_POWERPOINT, RENDERER_WPS):
        for prog_id in _RENDERER_PROG_IDS[renderer]:
            try:
                _probe_prog_id(prog_id, timeout=timeout_per_candidate)
                available.append({"renderer": renderer, "prog_id": prog_id, "available": True})
                break
            except Exception as exc:
                errors[prog_id] = str(exc)
    return {"available": available, "errors": errors}


def detect_presentation_renderer(timeout_per_candidate=12):
    result = detect_presentation_renderers(timeout_per_candidate=timeout_per_candidate)
    if result["available"]:
        selected = dict(result["available"][0])
        selected["errors"] = result["errors"]
        return selected
    return {
        "renderer": RENDERER_NONE,
        "prog_id": "",
        "available": False,
        "errors": result["errors"],
    }


def _natural_slide_key(path):
    name = os.path.basename(path)
    match = re.search(r"(\d+)(?=\D*$)", name)
    return (int(match.group(1)) if match else 10**9, name.casefold())


def export_presentation_pngs(
    source_path,
    output_dir,
    *,
    renderer,
    prog_id,
    width=1600,
    height=900,
    timeout=180,
):
    source = os.path.abspath(str(source_path or ""))
    target = os.path.abspath(str(output_dir or ""))
    renderer = str(renderer or "").strip().lower()
    prog_id = str(prog_id or "").strip()
    if not os.path.isfile(source) or os.path.splitext(source)[1].lower() != ".pptx":
        raise ValueError("请选择有效的 PPTX 文件。")
    if renderer not in {RENDERER_POWERPOINT, RENDERER_WPS}:
        raise ValueError("没有可用的 PowerPoint 或 WPS 演示渲染器。")
    if prog_id not in _RENDERER_PROG_IDS[renderer]:
        raise ValueError("演示渲染器 ProgID 与渲染器类型不匹配。")
    os.makedirs(target, exist_ok=True)
    for name in os.listdir(target):
        path = os.path.join(target, name)
        if os.path.isfile(path) and os.path.splitext(name)[1].lower() == ".png":
            os.remove(path)

    payload = base64.b64encode(
        json.dumps(
            {
                "prog_id": prog_id,
                "renderer": renderer,
                "source": source,
                "target": target,
                "width": max(320, int(width or 1600)),
                "height": max(180, int(height or 900)),
            },
            ensure_ascii=False,
        ).encode("utf-8")
    ).decode("ascii")
    script = f"""
$ErrorActionPreference = 'Stop'
$cfg = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{payload}')) | ConvertFrom-Json
$app = $null
$presentation = $null
try {{
    $type = [Type]::GetTypeFromProgID([string]$cfg.prog_id, $false)
    if ($null -eq $type) {{ throw "Automation ProgID 未注册: $($cfg.prog_id)" }}
    $app = [Activator]::CreateInstance($type)
    if ([string]$cfg.renderer -eq 'powerpoint') {{
        $presentation = $app.Presentations.Open([string]$cfg.source, -1, 0, 0)
    }} else {{
        $presentation = $app.Presentations.Open([string]$cfg.source, -1)
    }}
    if ($null -eq $presentation) {{ throw '演示文稿只读打开失败。' }}
    $presentation.Export([string]$cfg.target, 'PNG', [int]$cfg.width, [int]$cfg.height)
    [Console]::Out.WriteLine('OK')
}} finally {{
    if ($null -ne $presentation) {{ try {{ $presentation.Close() }} catch {{}} }}
    if ($null -ne $app) {{ try {{ $app.Quit() }} catch {{}} }}
}}
"""
    _run_powershell(script, timeout)
    exported = sorted(
        [
            os.path.join(target, name)
            for name in os.listdir(target)
            if os.path.isfile(os.path.join(target, name))
            and os.path.splitext(name)[1].lower() == ".png"
        ],
        key=_natural_slide_key,
    )
    if not exported:
        raise RuntimeError("演示应用未导出任何幻灯片 PNG。")
    normalized = []
    for index, path in enumerate(exported, start=1):
        destination = os.path.join(target, f"slide-{index:03d}.png")
        if os.path.normcase(path) != os.path.normcase(destination):
            if os.path.exists(destination):
                os.remove(destination)
            os.replace(path, destination)
        normalized.append(destination)
    return normalized
