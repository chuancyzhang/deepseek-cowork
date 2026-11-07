# -*- mode: python ; coding: utf-8 -*-
"""Native desktop builds. Windows continues to use deepseek-cowork.spec."""
import os
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

ROOT = Path(SPECPATH)
sys.path.insert(0, str(ROOT))
from core.app_version import APP_VERSION
from core.platform_capabilities import desktop_target, skill_unavailable_reason

target = desktop_target()
if target == "windows-x64":
    raise RuntimeError("Windows must use deepseek-cowork.spec")
datas = []
for name in ("skills", "ai_skills", "images", "web/editors/dist"):
    for path in (ROOT / name).rglob("*"):
        if not path.is_file() or any(part in {"node_modules", "__pycache__", ".git"} for part in path.parts):
            continue
        if name in {"skills", "ai_skills"} and skill_unavailable_reason(path.relative_to(ROOT / name).parts[0]):
            continue
        datas.append((str(path), str(path.parent.relative_to(ROOT))))
for name in ("config.json", "resources/enterprise_capabilities.json",
             "bundled_plugins/data_security/NOTICE.txt", "bundled_plugins/data_security/LICENSE.txt"):
    path = ROOT / name
    datas.append((str(path), str(path.parent.relative_to(ROOT))))
for distribution in ("keyring", "mcp", "qqbot-agent-sdk", "pyqtdarktheme"):
    datas += copy_metadata(distribution, recursive=True)
hidden = ["core." + path.stem for path in (ROOT / "core").glob("*.py") if path.stem != "__init__"]
for package in ("core.llm", "core.im_gateway", "bootstrap_plugins", "bundled_plugins.data_security", "mcp", "qqbot_agent_sdk"):
    hidden += collect_submodules(package)
hidden += ["PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineCore", "PySide6.QtWebChannel",
           "PySide6.QtPdfWidgets", "PySide6.QtPdf", "PySide6.QtQuickWidgets", "PySide6.QtSvg",
           "docx", "pptx", "openpyxl", "xlrd", "pypdf", "PIL", "reportlab", "Crypto.Cipher.AES",
           "keyring.backends.macOS" if target == "macos-arm64" else "keyring.backends.SecretService"]
a = Analysis([str(ROOT / "main.py")], pathex=[str(ROOT)], binaries=[], datas=datas,
             hiddenimports=hidden, hookspath=[], runtime_hooks=[],
             excludes=["winpty", "PyQt5", "PyQt6", "PySide2"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="deepseek-cowork",
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, target_arch="arm64" if target == "macos-arm64" else None,
          codesign_identity=os.environ.get("COWORK_CODESIGN_IDENTITY") or None,
          entitlements_file=str(ROOT / "resources/macos-entitlements.plist") if target == "macos-arm64" else None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="deepseek-cowork")
if target == "macos-arm64":
    app = BUNDLE(coll, name="DeepSeek Cowork.app", bundle_identifier="com.deepseekcowork.desktop",
                 info_plist={"CFBundleShortVersionString": APP_VERSION, "CFBundleVersion": APP_VERSION,
                             "NSHighResolutionCapable": True})
