from __future__ import annotations

import os
from pathlib import Path


def default_data_root() -> Path:
    override = os.getenv("QSM_DATA_ROOT", "").strip()
    if override:
        root = Path(override).expanduser().resolve()
    elif os.name == "nt":
        appdata = os.getenv("APPDATA", "").strip() or str(Path.home())
        root = Path(appdata) / "DeepSeekCowork" / "quant_strategy_management"
    elif os.name == "darwin":
        root = Path.home() / "Library" / "Application Support" / "DeepSeekCowork" / "quant_strategy_management"
    else:
        root = Path.home() / ".local" / "share" / "DeepSeekCowork" / "quant_strategy_management"
    root.mkdir(parents=True, exist_ok=True)
    return root


def ensure_path(path: str | Path | None = None) -> Path:
    root = Path(path).expanduser().resolve() if path else default_data_root()
    root.mkdir(parents=True, exist_ok=True)
    return root
