# -*- mode: python ; coding: utf-8 -*-
import sys
import os

block_cipher = None

python_prefix = sys.exec_prefix
# Bundle the Python environment (excluding unnecessary dev files)
# This allows the agent to execute code without requiring the user to install Python
# We exclude 'Scripts' to save space, but ensure 'Lib' and 'DLLs' are included.
python_env = Tree(python_prefix, prefix='python_env', excludes=['Doc', 'tcl', 'Tools', 'include', 'libs', 'Scripts', 'share', 'test', '__pycache__'])

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('skills', 'skills'), ('config.json', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SmartFileAssistant',
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SmartFileAssistant',
)
