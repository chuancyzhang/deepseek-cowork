"""Checks for the runtimes shipped in Windows builds; probes use isolated scratch space."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


DATA_ANALYSIS_DISTS = ("numpy", "pandas", "scipy", "matplotlib", "seaborn", "scikit-learn", "nbformat", "nbclient", "ipykernel")


def data_analysis_versions(lock_path):
    versions = {}
    for line in Path(lock_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, version = line.split("==")
        if not name or not version or name in versions:
            raise ValueError(f"Invalid data analysis dependency lock: {line}")
        versions[name] = version
    if not set(DATA_ANALYSIS_DISTS).issubset(versions):
        raise ValueError("Data analysis dependency lock is incomplete.")
    return versions


# Executed by the shipped interpreter, never the build interpreter. -I excludes
# the host's PYTHONPATH/user site; origins also have to belong to this bundle.
PYTHON_BUNDLE_PROBE = r'''
import importlib, importlib.metadata as metadata, io, json, pathlib, socket, sys, tempfile, ipaddress
root = pathlib.Path(sys.executable).resolve().parent
original_connect = socket.socket.connect
def no_network(self, address, *args, **kwargs):
    if isinstance(address, tuple) and ipaddress.ip_address(address[0]).is_loopback:
        return original_connect(self, address, *args, **kwargs)
    raise RuntimeError("Bundled Python validation must not access external networks")
socket.socket.connect = no_network
expected = json.loads(sys.argv[1])
for name, version in expected.items():
    if metadata.version(name) != version:
        raise RuntimeError(f"{name}: expected {version}, found {metadata.version(name)}")
for name in ('numpy', 'pandas', 'scipy', 'matplotlib', 'seaborn', 'sklearn',
             'nbformat', 'nbclient', 'ipykernel', 'zmq', 'openpyxl', 'xlrd', 'docx', 'pptx', 'PIL.Image', 'pypdf', 'pypdfium2', 'pypdfium2_raw', 'reportlab'):
    module = importlib.import_module(name)
    if not pathlib.Path(module.__file__).resolve().is_relative_to(root):
        raise RuntimeError(f"{name} loaded outside bundled Python: {module.__file__}")
import numpy as np, pandas as pd
from scipy.linalg import solve
from sklearn.linear_model import LinearRegression
from joblib import Parallel, delayed
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns
np.testing.assert_allclose(solve([[3., 1.], [1., 2.]], [9., 8.]), [2., 3.])
model = LinearRegression().fit(np.array([[0.], [1.], [2.]]), [1., 3., 5.])
np.testing.assert_allclose(model.predict([[3.]]), [7.])
if Parallel(n_jobs=2)(delayed(pow)(x, 2) for x in range(3)) != [0, 1, 4]:
    raise RuntimeError('Joblib worker execution failed')
frame = pd.DataFrame({'group': ['a', 'a', 'b'], 'value': [1, 2, 3]})
if frame.groupby('group')['value'].sum().to_dict() != {'a': 3, 'b': 3}:
    raise RuntimeError('pandas aggregation failed')
frame.to_excel('data.xlsx', index=False)
pd.testing.assert_frame_equal(pd.read_excel('data.xlsx'), frame)
sns.barplot(data=frame, x='group', y='value', errorbar=None)
plt.savefig('plot.png'); plt.close('all')
from PIL import Image
with Image.open('plot.png') as picture:
    picture.verify()
from docx import Document
document = Document(); document.add_paragraph('Cowork'); document.save('test.docx')
if Document('test.docx').paragraphs[0].text != 'Cowork':
    raise RuntimeError('Word round-trip failed')
from pptx import Presentation
deck = Presentation(); deck.slides.add_slide(deck.slide_layouts[6]); deck.save('test.pptx')
if len(Presentation('test.pptx').slides) != 1:
    raise RuntimeError('PowerPoint round-trip failed')
from reportlab.pdfgen.canvas import Canvas
from pypdf import PdfReader
canvas = Canvas('test.pdf'); canvas.drawString(72, 72, 'Cowork'); canvas.save()
if 'Cowork' not in PdfReader('test.pdf').pages[0].extract_text():
    raise RuntimeError('PDF round-trip failed')
if metadata.version('pypdfium2') != '5.15.2':
    raise RuntimeError('PDFium binding version does not match the release')
import pypdfium2 as pdfium
pdf = pdfium.PdfDocument('test.pdf')
page = pdf[0]
bitmap = page.render(scale=1)
picture = bitmap.to_pil()
picture.save('page.png')
picture.close(); bitmap.close(); page.close(); pdf.close()
with Image.open('page.png') as picture:
    picture.verify()
import nbformat
from nbclient import NotebookClient
from jupyter_client import AsyncKernelManager
from jupyter_client.kernelspec import KernelSpecManager
with tempfile.TemporaryDirectory() as temporary:
    spec = pathlib.Path(temporary) / 'cowork-python'; spec.mkdir()
    (spec / 'kernel.json').write_text(json.dumps({'argv': [sys.executable, '-m', 'ipykernel_launcher', '-f', '{connection_file}'], 'display_name': 'Cowork Python', 'language': 'python'}), encoding='utf-8')
    notebook = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell('print(6 * 7)')])
    manager = AsyncKernelManager(kernel_name='cowork-python', kernel_spec_manager=KernelSpecManager(kernel_dirs=[temporary]))
    NotebookClient(notebook, km=manager, timeout=15, force_raise_errors=True).execute(cleanup_kc=True)
    nbformat.validate(notebook)
    if notebook.cells[0].outputs[0].text.strip() != '42':
        raise RuntimeError('Notebook kernel execution failed')
    nbformat.write(notebook, 'executed.ipynb')
print(json.dumps({'versions': expected, 'checks': ['imports', 'linear-algebra', 'regression', 'joblib-workers', 'aggregation', 'xlsx', 'plot', 'docx', 'pptx', 'pdf', 'pdf-raster', 'notebook-kernel']}))
'''


def validate_python_bundle(root: Path) -> dict:
    root = Path(root).resolve()
    versions = data_analysis_versions(root / "requirements-data-analysis.txt")
    expected = data_analysis_versions(Path(__file__).resolve().parents[1] / "requirements-data-analysis.txt")
    if versions != expected:
        raise ValueError("Bundled data analysis versions do not match this release's dependency lock.")
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("PYTHON", "PIP_", "MPL", "QT_"))}
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    env["PATH"] = os.pathsep.join(map(str, [root, root / "DLLs", windows / "System32", windows]))
    scratch = Path(__file__).resolve().parents[1] / ".tmp" / "python-bundle-check"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="probe-", dir=scratch) as directory:
        env.update(TEMP=directory, TMP=directory, MPLCONFIGDIR=directory,
                   APPDATA=directory, LOCALAPPDATA=directory, USERPROFILE=directory)
        result = subprocess.run(
            [str(root / "python.exe"), "-I", "-B", "-X", "utf8", "-c", PYTHON_BUNDLE_PROBE, json.dumps(versions)],
            cwd=directory, env=env, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode:
            raise ValueError(f"Bundled Python verification failed ({result.returncode}):\n{result.stderr or result.stdout}")
        try:
            return json.loads(result.stdout)
        except ValueError as exc:
            raise ValueError(f"Bundled Python returned an invalid verification result: {result.stdout}") from exc


RIPGREP_VERSION = "15.1.0"
RIPGREP_ARCHIVE = "ripgrep-15.1.0-x86_64-pc-windows-msvc.zip"
RIPGREP_URL = f"https://github.com/BurntSushi/ripgrep/releases/download/{RIPGREP_VERSION}/{RIPGREP_ARCHIVE}"
RIPGREP_ARCHIVE_SHA256 = "124510B94B6BAA3380D051FDF4650EAA80A302C876D611E9DBA0B2E18D87493A"
RIPGREP_FILE_HASHES = {
    "rg.exe": "DECDD4992F3F1B9A5EF9898F1B40AB16886D579D6516B4EFD3D5EAA19364E408",
    "COPYING": "DFE7D0A6134A17D3DE7409762E08DC02133303912875CAB40A74BA07A390F85A",
    "LICENSE-MIT": "970813655A1BF777D2EAD189CEF71AB73AB92CE04DC864027D61A45DE03E6728",
    "UNLICENSE": "640514163B17F977ADC997CB16F51871122CFB0555EBAD1A3F01E167B7BA8857",
}
GIT_REQUIRED_FILES = (
    "bin/bash.exe",
    "cmd/git.exe",
    "etc/gitconfig",
    "mingw64/bin/git-credential-manager.exe",
    "mingw64/bin/git-credential-manager.exe.config",
    "mingw64/bin/gcmcore.dll",
    "mingw64/bin/Avalonia.Skia.dll",
    "mingw64/bin/SkiaSharp.dll",
    "mingw64/bin/libSkiaSharp.dll",
)


def _require_files(root: Path, names, label: str):
    missing = [name for name in names if not (root / name).is_file()]
    if missing:
        raise ValueError(
            f"Missing {label} runtime files at {root}: {', '.join(missing)}. "
            "Run scripts/fetch_runtimes.ps1 before building."
        )


def validate_ripgrep_bundle(root: Path) -> dict:
    root = Path(root)
    _require_files(root, RIPGREP_FILE_HASHES, "ripgrep")
    for name, expected in RIPGREP_FILE_HASHES.items():
        digest = hashlib.sha256()
        with (root / name).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        actual = digest.hexdigest().upper()
        if actual != expected:
            raise ValueError(
                f"ripgrep {RIPGREP_VERSION} SHA256 mismatch for {name}: "
                f"expected={expected} actual={actual}"
            )
    return {
        "version": RIPGREP_VERSION,
        "sha256": RIPGREP_FILE_HASHES["rg.exe"],
        "bytes": (root / "rg.exe").stat().st_size,
    }


def _git_config_values(root: Path, key: str) -> list[str]:
    # Query only the shipped file; never read a developer's per-user config.
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_CONFIG")}
    result = subprocess.run(
        [str(root / "cmd/git.exe"), "config", "--file", str(root / "etc/gitconfig"),
         "--no-includes", "--get-all", key],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=10, env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode not in (0, 1):
        raise ValueError(f"Cannot inspect bundled Git {key}: {result.stderr.strip()}")
    return result.stdout.splitlines()


def validate_git_bundle(root: Path) -> dict:
    root = Path(root)
    _require_files(root, GIT_REQUIRED_FILES, "Git / Credential Manager")
    helpers = _git_config_values(root, "credential.helper")
    if helpers != ["manager"]:
        raise ValueError(
            f"Bundled Git must default to credential.helper=manager; found {helpers!r} in "
            f"{root / 'etc/gitconfig'}. Run scripts/fetch_runtimes.ps1 before building."
        )
    for key in ("credential.interactive", "credential.guiPrompt"):
        if any(value.lower() in {"false", "never", "no", "0"} for value in _git_config_values(root, key)):
            raise ValueError(f"Bundled Git disables account authorization with {key}.")
    return {"credential_helper": "manager", "gui_authentication": True}
