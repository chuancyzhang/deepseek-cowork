"""Shared isolated temporary paths for standalone QA."""

import os
from pathlib import Path
import tempfile


def prepare_qa_directory(script: str) -> Path:
    source = Path(script).resolve()
    base = Path(os.environ.get("COWORK_QA_ROOT") or source.parent.parent / ".tmp").resolve()
    root = base / source.stem
    temporary = root / "temp"
    temporary.mkdir(parents=True, exist_ok=True)
    os.environ["TEMP"] = str(temporary)
    os.environ["TMP"] = str(temporary)
    tempfile.tempdir = str(temporary)
    return root
