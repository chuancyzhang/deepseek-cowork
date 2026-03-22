# -*- mode: python ; coding: utf-8 -*-
import fnmatch
import os
import sys

block_cipher = None

python_prefix = sys.exec_prefix


def _add_data_file(store, src, dest):
    if not src or not os.path.isfile(src):
        return
    normalized = (dest.replace("\\", "/"), os.path.abspath(src), "DATA")
    if normalized not in store:
        store.append(normalized)


def _collect_tree(src_root, dest_root, exclude_dirs=None, exclude_globs=None):
    exclude_dirs = set(exclude_dirs or [])
    exclude_globs = exclude_globs or []
    collected = []
    if not os.path.isdir(src_root):
        return collected
    for current_root, dirs, files in os.walk(src_root):
        dirs[:] = [
            d for d in dirs
            if d not in exclude_dirs and d != "__pycache__"
        ]
        rel_root = os.path.relpath(current_root, src_root)
        rel_root = "" if rel_root == "." else rel_root
        for name in files:
            rel_path = os.path.normpath(os.path.join(rel_root, name))
            if any(fnmatch.fnmatch(rel_path, pattern) for pattern in exclude_globs):
                continue
            src = os.path.join(current_root, name)
            dest = os.path.join(dest_root, rel_path).replace("\\", "/")
            collected.append((dest, src, "DATA"))
    return collected


def _collect_minimal_python_env(prefix):
    datas = []

    root_candidates = [
        "python.exe",
        "pythonw.exe",
        "pyvenv.cfg",
        "python3.dll",
        "python312.dll",
        "python313.dll",
        "python314.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
    ]
    for name in root_candidates:
        _add_data_file(datas, os.path.join(prefix, name), f"python_env/{name}")

    scripts_dir = os.path.join(prefix, "Scripts")
    for name in ("python.exe", "pythonw.exe"):
        _add_data_file(datas, os.path.join(scripts_dir, name), f"python_env/{name}")

    lib_dir = os.path.join(prefix, "Lib")
    datas.extend(
        _collect_tree(
            lib_dir,
            "python_env/Lib",
            exclude_dirs={"site-packages", "test", "tests", "idlelib", "tkinter", "turtledemo"},
        )
    )

    site_packages = os.path.join(lib_dir, "site-packages")
    minimal_site_packages = [
        "pip",
        "pip-*",
        "setuptools",
        "setuptools-*",
        "wheel",
        "wheel-*",
        "pkg_resources",
        "distlib",
        "distlib-*",
    ]
    for item in minimal_site_packages:
        for matched in sorted(fnmatch.filter(os.listdir(site_packages), item)) if os.path.isdir(site_packages) else []:
            src_path = os.path.join(site_packages, matched)
            dest_path = f"python_env/Lib/site-packages/{matched}"
            if os.path.isdir(src_path):
                datas.extend(_collect_tree(src_path, dest_path))
            else:
                _add_data_file(datas, src_path, dest_path)

    return datas


python_env = _collect_minimal_python_env(python_prefix)

pyside6_hidden = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
]

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
        'bs4',
        'requests',
        'markdown',
        'qtawesome',
        'anthropic',
        'openai',
        'lark_oapi'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'pandas', 
        'numpy', 
        'duckduckgo_search', 
        'matplotlib',
        'scipy',
        'lxml',
        'PySide6.Qt3DAnimation',
        'PySide6.Qt3DCore',
        'PySide6.Qt3DExtras',
        'PySide6.Qt3DInput',
        'PySide6.Qt3DLogic',
        'PySide6.Qt3DRender',
        'PySide6.QtAxContainer',
        'PySide6.QtCharts',
        'PySide6.QtConcurrent',
        'PySide6.QtDataVisualization',
        'PySide6.QtDesigner',
        'PySide6.QtGraphs',
        'PySide6.QtHttpServer',
        'PySide6.QtLocation',
        'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets',
        'PySide6.QtNetworkAuth',
        'PySide6.QtOpenGL',
        'PySide6.QtOpenGLWidgets',
        'PySide6.QtPdf',
        'PySide6.QtPdfWidgets',
        'PySide6.QtPositioning',
        'PySide6.QtQml',
        'PySide6.QtQmlCompiler',
        'PySide6.QtQmlCore',
        'PySide6.QtQmlMeta',
        'PySide6.QtQmlModels',
        'PySide6.QtQmlWorkerScript',
        'PySide6.QtQuick',
        'PySide6.QtQuick3D',
        'PySide6.QtQuickControls2',
        'PySide6.QtQuickDialogs2',
        'PySide6.QtQuickEffects',
        'PySide6.QtQuickLayouts',
        'PySide6.QtQuickParticles',
        'PySide6.QtQuickShapes',
        'PySide6.QtQuickTemplates2',
        'PySide6.QtQuickTest',
        'PySide6.QtQuickWidgets',
        'PySide6.QtRemoteObjects',
        'PySide6.QtScxml',
        'PySide6.QtSensors',
        'PySide6.QtSerialBus',
        'PySide6.QtSerialPort',
        'PySide6.QtSpatialAudio',
        'PySide6.QtStateMachine',
        'PySide6.QtSvg',
        'PySide6.QtSvgWidgets',
        'PySide6.QtTest',
        'PySide6.QtTextToSpeech',
        'PySide6.QtUiTools',
        'PySide6.QtWebChannel',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineQuick',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebSockets',
        'PySide6.QtWebView',
        'PySide6.QtXml',
        'PySide6.QtXmlPatterns',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
print(f"Bundled minimal python env entries: {len(python_env)}")
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
