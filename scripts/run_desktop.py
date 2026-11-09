"""Build and launch the Electron workspace using the selected Python environment."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-build", action="store_true", help="Launch an already built workspace")
    parser.add_argument("--data-dir", type=Path, help="Use an isolated absolute data directory")
    args = parser.parse_args()
    desktop = ROOT / "desktop"
    node = ROOT / "node_env" / ("node.exe" if os.name == "nt" else "bin/node")
    node = str(node) if node.is_file() else shutil.which("node")
    if not node:
        raise SystemExit("缺少 Node.js，请安装 Node.js 22.12+ 后重试。")
    if not (desktop / "node_modules" / "vite" / "bin" / "vite.js").is_file():
        raise SystemExit("请先在 desktop 目录运行 npm ci，安装锁定的前端依赖。")
    env = {**os.environ, "COWORK_PYTHON": sys.executable, "PYTHONUTF8": "1"}
    env.pop("ELECTRON_RUN_AS_NODE", None)
    if args.data_dir:
        env["COWORK_APP_DATA_DIR"] = str(args.data_dir.resolve())
    if not args.no_build:
        for script, extra in [("typescript/bin/tsc", ["--noEmit"]), ("vite/bin/vite.js", ["build"])]:
            subprocess.run([node, str(desktop / "node_modules" / script), *extra], cwd=desktop, env=env, check=True)
    electron = desktop / "node_modules/electron/dist" / ("electron.exe" if os.name == "nt" else "Electron.app/Contents/MacOS/Electron" if sys.platform == "darwin" else "electron")
    if not electron.is_file():
        raise SystemExit("Electron 运行时不完整，请在 desktop 目录运行 npm rebuild electron。")
    return subprocess.call([str(electron), str(desktop)], cwd=ROOT, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
