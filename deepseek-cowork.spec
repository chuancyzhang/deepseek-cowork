# -*- mode: python ; coding: utf-8 -*-
import sys
import os
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

python_prefix = sys.exec_prefix
python_env = Tree(
    python_prefix,
    prefix='python_env',
    excludes=['Doc', 'tcl', 'Tools', 'include', 'libs', 'Scripts', 'share', 'test', '__pycache__']
)

pyside6_hidden = collect_submodules('PySide6')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('skills', 'skills'), ('config.json', '.'), ('images', 'images')],
    hiddenimports=pyside6_hidden + [
        'docx',
        'pptx',
        'openpyxl',
        'pypdf',
        'pandas',
        'duckduckgo_search',
        'trafilatura',
        'bs4',
        'requests'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
print(f"Python Env Tree size: {len(python_env)}")
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='deepseek-cowork',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='images/logo.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas + python_env,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='deepseek-cowork',
)
