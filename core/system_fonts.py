"""System font inventory without initializing a GUI toolkit."""
import os
import shutil
import subprocess


def installed_font_families():
    if os.name != "nt":
        executable = shutil.which("fc-list")
        if not executable:
            raise RuntimeError("当前系统未提供字体目录查询服务。")
        result = subprocess.run([executable, "--format", "%{family}\n"],
                                capture_output=True, text=True, timeout=15, check=True)
        return sorted({name.strip() for row in result.stdout.splitlines()
                       for name in row.split(",") if name.strip()}, key=str.casefold)

    import ctypes
    from ctypes import wintypes
    class LogFont(ctypes.Structure):
        _fields_ = [(name, wintypes.LONG) for name in ("height", "width", "escapement", "orientation", "weight")] + [
            (name, wintypes.BYTE) for name in ("italic", "underline", "strikeout", "charset", "out_precision", "clip_precision", "quality", "pitch")
        ] + [("face", wintypes.WCHAR * 32)]
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.POINTER(LogFont), ctypes.c_void_p, wintypes.DWORD, wintypes.LPARAM)
    gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    gdi.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi.CreateCompatibleDC.restype = wintypes.HDC
    gdi.DeleteDC.argtypes = [wintypes.HDC]
    gdi.EnumFontFamiliesExW.argtypes = [wintypes.HDC, ctypes.POINTER(LogFont), callback_type, wintypes.LPARAM, wintypes.DWORD]
    names = set()
    @callback_type
    def collect(font, _metrics, _kind, _parameter):
        name = font.contents.face
        if name and not name.startswith("@"):
            names.add(name)
        return 1
    device = gdi.CreateCompatibleDC(None)
    if not device:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        query = LogFont()
        query.charset = 1
        gdi.EnumFontFamiliesExW(device, ctypes.byref(query), collect, 0, 0)
    finally:
        gdi.DeleteDC(device)
    if not names:
        raise RuntimeError("系统字体目录为空，无法校验主题字体。")
    return sorted(names, key=str.casefold)
