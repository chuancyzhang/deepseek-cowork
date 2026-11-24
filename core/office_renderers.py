"""Discover local renderers without activating COM; inspect interrupted helpers first."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import logging

log = logging.getLogger(__name__)
PROGIDS = {
    'word': ('Word.Application', 'KWPS.Application', 'WPS.Application'),
    'excel': ('Excel.Application', 'KET.Application', 'ET.Application'),
    'powerpoint': ('PowerPoint.Application', 'KWPP.Application', 'WPP.Application'),
}
NAMES = {'word': ('WINWORD.exe',), 'excel': ('EXCEL.exe',), 'powerpoint': ('POWERPNT.exe',),
         'wps': ('wps.exe', 'et.exe', 'wpp.exe', 'wpsclouddsvr.exe')}


def state_root():
    from .env_utils import get_app_data_dir
    root = Path(get_app_data_dir()) / 'browser_previews' / 'native-state'
    root.mkdir(parents=True, exist_ok=True)
    return root


def state_path(group):
    if group not in NAMES:
        raise ValueError('Invalid renderer group')
    return state_root() / (group + '.json')


def _registered(progid):
    if os.name != 'nt':
        return None
    import winreg
    windows = Path(os.environ.get('SystemRoot', r'C:\Windows'))
    for flag, host, bits in ((winreg.KEY_WOW64_64KEY, 'System32', 64), (winreg.KEY_WOW64_32KEY, 'SysWOW64', 32)):
        def read(key):
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, key, 0, winreg.KEY_READ | flag) as handle:
                return winreg.QueryValueEx(handle, '')[0]
        try:
            clsid = read(progid + r'\CLSID')
            command = str(read('CLSID\\' + clsid + r'\LocalServer32'))
            match = re.match(r'^\s*"?(.+?\.exe)(?:"|\s|$)', command, re.I)
            binary = Path(os.path.expandvars(match[1])) if match else None
            if not binary or not binary.is_file():
                continue
            stat = binary.stat()
            version = hashlib.sha256(f'{progid}|{bits}|{command}|{stat.st_size}|{stat.st_mtime_ns}'.encode()).hexdigest()
            return {'progid': progid, 'bits': bits, 'version': version, 'executable': str(binary),
                    'host': str(windows / host / 'WindowsPowerShell/v1.0/powershell.exe')}
        except OSError:
            continue
    return None


def _mutex_busy(group):
    if os.name != 'nt':
        return False
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenMutexW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
    kernel.OpenMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.OpenMutexW(0x00100001, False, 'Local\\CoworkOfficePreview-' + group)
    if not handle:
        if ctypes.get_last_error() == 2:
            return False
        raise OSError(ctypes.get_last_error(), '无法核对本机办公软件任务状态')
    try:
        result = kernel.WaitForSingleObject(handle, 0)
        if result in (0, 0x80):
            kernel.ReleaseMutex(handle)
            return False
        if result == 0x102:
            return True
        raise OSError(ctypes.get_last_error(), '无法核对本机办公软件任务状态')
    finally:
        kernel.CloseHandle(handle)


def _office_processes(group):
    """Only used to confirm no old Office instance survives, never to kill one."""
    from ctypes import wintypes
    class Entry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('pid', wintypes.DWORD),
                    ('heap', ctypes.c_size_t), ('module', wintypes.DWORD), ('threads', wintypes.DWORD),
                    ('parent', wintypes.DWORD), ('priority', wintypes.LONG), ('flags', wintypes.DWORD),
                    ('name', wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ('Process32FirstW', 'Process32NextW'):
        getattr(kernel, name).argtypes = (wintypes.HANDLE, ctypes.POINTER(Entry))
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), '无法核对办公软件进程')
    try:
        entry = Entry(); entry.dwSize = ctypes.sizeof(entry)
        active = kernel.Process32FirstW(handle, ctypes.byref(entry))
        if not active:
            raise OSError(ctypes.get_last_error(), '无法枚举办公软件进程')
        found = []
        names = {name.casefold() for name in NAMES[group]}
        while active:
            if entry.name.casefold() in names:
                found.append(entry.pid)
            active = kernel.Process32NextW(handle, ctypes.byref(entry))
        return found
    finally:
        kernel.CloseHandle(handle)


def native_state(group):
    """No COM call here. A timeout cannot be cleared just by retrying."""
    marker = state_path(group)
    if _mutex_busy(group):
        return {'state': 'busy', 'reason': '本机办公软件仍有转换任务，可能存在待处理弹窗，请切到对应窗口查看。'}
    if marker.exists():
        # The helper failed to confirm document cleanup. Only absence of the old
        # application permits revalidation; a live process is never a health check.
        if os.name == 'nt' and not _office_processes(group):
            marker.unlink()
            log.info('office_renderer.recovery group=%s old_instance_exited=true', group)
        else:
            return {'state': 'blocked', 'reason': '上次转换尚未确认清理完成。请先在本机应用保存工作并关闭对应软件，再重新检测。'}
    return {'state': 'ready', 'reason': ''}


def renderer_catalog(family):
    if family not in PROGIDS:
        raise ValueError('此格式没有本机页面预览。')
    candidates = []
    for kind, ids in (('office', PROGIDS[family][:1]), ('wps', PROGIDS[family][1:])):
        registered = next((value for progid in ids if (value := _registered(progid))), None)
        if not registered:
            continue
        group = 'wps' if kind == 'wps' else family
        state = native_state(group)
        evidence = state_root() / (registered['version'] + '-' + family + '.verified.json')
        candidates.append({**registered, **state, 'id': kind, 'group': group, 'available': state['state'] == 'ready',
                           'name': 'Microsoft ' + {'word': 'Word', 'excel': 'Excel', 'powerpoint': 'PowerPoint'}[family] if kind == 'office' else 'WPS',
                           'verified': evidence.is_file()})
    from .libreoffice_component import status
    libreoffice = status()
    if libreoffice.get('available'):
        candidates.append({'id': 'libreoffice', 'name': 'LibreOffice', 'available': True, 'state': 'ready', 'reason': ''})
    return {'available': any(item['available'] for item in candidates), 'candidates': candidates,
            'install_supported': libreoffice.get('install_supported', False)}


def select_renderer(family, requested='auto'):
    from .workbench import WorkbenchError
    if requested not in {'auto', 'office', 'wps', 'libreoffice'}:
        raise ValueError('不支持的文档渲染器。')
    if requested == 'libreoffice':
        return {'id': 'libreoffice', 'name': 'LibreOffice'}
    catalog = renderer_catalog(family)
    candidates = catalog['candidates']
    # Never silently bypass a blocked preferred engine with a different one.
    selected = next((item for item in candidates if requested == 'auto' or item['id'] == requested), None)
    if not selected:
        raise WorkbenchError('renderer_unavailable', '没有可用的所选渲染方式。请安装办公软件或 LibreOffice，也可以用本机应用打开文件。')
    if not selected['available']:
        raise WorkbenchError('renderer_cleanup_pending', selected['reason'])
    return selected
