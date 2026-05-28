# -*- mode: python ; coding: utf-8 -*-
import fnmatch
import os
import sys

block_cipher = None

_spec_arg = next((arg for arg in sys.argv[1:] if str(arg).lower().endswith(".spec")), None)
SPEC_DIR = os.path.dirname(os.path.abspath(_spec_arg)) if _spec_arg else os.getcwd()
ICON_PATH = os.path.join(SPEC_DIR, "images", "logo.ico")

python_prefix = sys.exec_prefix
python_runtime_prefix = getattr(sys, "base_prefix", "") or python_prefix
PYSIDE6_ROOT = os.path.join(python_prefix, "Lib", "site-packages", "PySide6")


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
            rel_path_unix = rel_path.replace("\\", "/")
            if any(
                fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(rel_path_unix, pattern)
                for pattern in exclude_globs
            ):
                continue
            src = os.path.join(current_root, name)
            dest = os.path.join(dest_root, rel_path).replace("\\", "/")
            collected.append((dest, src, "DATA"))
    return collected


def _add_analysis_data_file(store, src, dest):
    if not src or not os.path.isfile(src):
        return
    normalized = (os.path.abspath(src), dest.replace("\\", "/"))
    if normalized not in store:
        store.append(normalized)


def _collect_tree_for_analysis(src_root, dest_root, exclude_dirs=None, exclude_globs=None):
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
            dest_dir = os.path.join(dest_root, rel_root).replace("\\", "/")
            collected.append((src, dest_dir))
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

    # Windows stdlib extension modules such as _socket and _ssl live outside Lib/.
    # Without these .pyd files, the bundled runtime can start but pip/network code fails.
    runtime_extension_dirs = [
        ("DLLs", "python_env/DLLs"),
        (os.path.join("Lib", "lib-dynload"), "python_env/Lib/lib-dynload"),
    ]
    for rel_src, dest_root in runtime_extension_dirs:
        datas.extend(
            _collect_tree(
                os.path.join(prefix, rel_src),
                dest_root,
            )
        )

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


python_env = _collect_minimal_python_env(python_runtime_prefix)


def _env_flag(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


ALLOW_MISSING_RUNTIMES = _env_flag("COWORK_ALLOW_MISSING_RUNTIMES", False)
RUNTIME_SLIM = _env_flag("COWORK_RUNTIME_SLIM", True)

def _collect_required_runtime_env(env_var, local_folder, dest_root, runtime_name, required_files=None, exclude_globs=None):
    src_root = os.environ.get(env_var) or os.path.join(SPEC_DIR, local_folder)
    if not os.path.isdir(src_root):
        if ALLOW_MISSING_RUNTIMES:
            print(
                f"[WARN] Missing {runtime_name} runtime directory: {src_root}. "
                f"Continuing because COWORK_ALLOW_MISSING_RUNTIMES=1."
            )
            return []
        raise FileNotFoundError(
            f"Missing required runtime '{runtime_name}' at: {src_root}. "
            f"Set {env_var} to a valid directory, or place runtime under '{local_folder}'. "
            f"Temporary bypass: set COWORK_ALLOW_MISSING_RUNTIMES=1 for this build."
        )
    required_files = required_files or []
    missing_files = [rel for rel in required_files if not os.path.isfile(os.path.join(src_root, rel))]
    if missing_files:
        if ALLOW_MISSING_RUNTIMES:
            print(
                f"[WARN] Runtime '{runtime_name}' is incomplete at {src_root}, "
                f"missing files: {missing_files}. Continuing because COWORK_ALLOW_MISSING_RUNTIMES=1."
            )
            return []
        raise FileNotFoundError(
            f"Runtime '{runtime_name}' is incomplete at: {src_root}. "
            f"Missing required files: {missing_files}. "
            f"Temporary bypass: set COWORK_ALLOW_MISSING_RUNTIMES=1 for this build."
        )
    return _collect_tree(
        src_root,
        dest_root,
        exclude_dirs={"__pycache__"},
        exclude_globs=exclude_globs or [],
    )


NODE_SLIM_EXCLUDES = [
    "CHANGELOG.md",
    "README.md",
    "LICENSE",
    "install_tools.bat",
    "*.ps1",
]

GIT_BASH_SLIM_EXCLUDES = [
    "usr/share/doc/*",
    "usr/share/man/*",
    "usr/share/info/*",
    "usr/share/gtk-doc/*",
    "usr/share/vim/*",
    "usr/share/nano/*",
    "mingw64/share/doc/*",
    "mingw64/share/man/*",
    "mingw64/share/info/*",
    "mingw64/share/locale/*",
    "usr/bin/vim*",
    "usr/bin/view.exe",
    "usr/bin/vimdiff.exe",
    "usr/bin/rvim.exe",
    "usr/bin/rview.exe",
    "usr/bin/xxd.exe",
    "mingw64/bin/git-lfs*",
    "mingw64/libexec/git-core/git-lfs*",
    "mingw64/bin/libSkiaSharp.dll",
    "mingw64/libexec/git-core/libSkiaSharp.dll",
]


node_env = _collect_required_runtime_env(
    "COWORK_NODE_DIR",
    "node_env",
    "node_env",
    "Node.js",
    required_files=["node.exe"],
    exclude_globs=NODE_SLIM_EXCLUDES if RUNTIME_SLIM else [],
)
git_bash_env = _collect_required_runtime_env(
    "COWORK_GIT_BASH_DIR",
    "git_bash_env",
    "git_bash_env",
    "Git Bash",
    required_files=[os.path.join("bin", "bash.exe")],
    exclude_globs=GIT_BASH_SLIM_EXCLUDES if RUNTIME_SLIM else [],
)


def _collect_minimal_pyside6():
    datas = []
    if not os.path.isdir(PYSIDE6_ROOT):
        return datas

    plugin_dirs = [
        "platforms",
        "styles",
        "imageformats",
        "iconengines",
        "platforminputcontexts",
    ]
    for plugin_dir in plugin_dirs:
        datas.extend(
            _collect_tree_for_analysis(
                os.path.join(PYSIDE6_ROOT, "plugins", plugin_dir),
                f"PySide6/plugins/{plugin_dir}",
            )
        )

    translation_files = [
        "qtbase_zh_CN.qm",
        "qtbase_en.qm",
        "qt_zh_CN.qm",
        "qt_en.qm",
    ]
    for name in translation_files:
        _add_analysis_data_file(
            datas,
            os.path.join(PYSIDE6_ROOT, "translations", name),
            "PySide6/translations",
        )

    return datas


qt_minimal_datas = _collect_minimal_pyside6()

pyside6_hidden = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtNetwork",
    "PySide6.QtWidgets",
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('skills', 'skills'), ('config.json', '.'), ('images', 'images'), ('qt.conf', '.')] + qt_minimal_datas,
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
print(f"Runtime slim mode: {'ON' if RUNTIME_SLIM else 'OFF'}")
print(f"Bundled node env entries: {len(node_env)}")
print(f"Bundled git bash env entries: {len(git_bash_env)}")
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
    icon=ICON_PATH,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas + python_env + node_env + git_bash_env,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='deepseek-cowork',
)
