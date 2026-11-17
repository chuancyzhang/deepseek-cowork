"""Execute a Python notebook with the shipped interpreter and a fresh kernel.

Run through run_skill_script so the host owns authorization, timeout and cancellation.
Only completed cells are guaranteed to survive forced process-tree cancellation.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import sys
import tempfile


def execute(source, output, *, workspace, cell_timeout=120, overwrite=False):
    import nbformat
    from nbclient import NotebookClient
    from jupyter_client import AsyncKernelManager
    from jupyter_client.kernelspec import KernelSpecManager

    workspace = Path(workspace).resolve(strict=True)
    def located(value):
        path = (workspace / value).resolve()
        if not path.is_relative_to(workspace):
            raise ValueError("Notebook files must be in the current workspace.")
        return path
    source, output = located(source), located(output)
    if source == output:
        raise ValueError("Use a separate output notebook to preserve the input.")
    if output.exists() and not overwrite:
        raise ValueError("Output already exists; choose a new path or explicitly pass --overwrite.")
    if source.stat().st_size > 50 * 1024 * 1024 or not 1 <= cell_timeout <= 3600:
        raise ValueError("Notebook must be <=50 MiB; cell timeout must be 1..3600 seconds.")
    nb = nbformat.read(source, as_version=4)
    nbformat.validate(nb)
    language = nb.metadata.get("kernelspec", {}).get("language", "python")
    if language.casefold() != "python":
        raise ValueError("This entry executes Python notebooks only.")
    for cell in nb.cells:
        if cell.cell_type == "code":
            cell.outputs, cell.execution_count = [], None
            cell.metadata.pop("execution", None)
    nb.metadata["cowork_execution"] = {"status": "running", "cell": None}
    output.parent.mkdir(parents=True, exist_ok=True)
    def save():
        # Replacing a sibling temporary file retains the last complete checkpoint.
        fd, temporary = tempfile.mkstemp(prefix=".notebook-", suffix=".ipynb", dir=output.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                nbformat.write(nb, handle)
            os.replace(temporary, output)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    def before(cell, cell_index, **_):
        nb.metadata["cowork_execution"]["cell"] = cell_index + 1
        save()
        print(json.dumps({"stage": "cell", "cell": cell_index + 1}), flush=True)
    def after(**_):
        save()

    save()
    print(json.dumps({"stage": "start", "cells": len(nb.cells)}), flush=True)
    with tempfile.TemporaryDirectory(prefix="cowork-notebook-kernel-") as temporary:
        kernel_dir = Path(temporary) / "cowork-python"
        kernel_dir.mkdir()
        (kernel_dir / "kernel.json").write_text(json.dumps({
            "argv": [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"],
            "display_name": "Cowork Python", "language": "python"}), encoding="utf-8")
        manager = AsyncKernelManager(kernel_name="cowork-python", autorestart=False,
                                kernel_spec_manager=KernelSpecManager(kernel_dirs=[temporary]))
        client = NotebookClient(nb, km=manager, timeout=cell_timeout, allow_errors=False, force_raise_errors=True,
                                skip_cells_with_tag="", raise_on_iopub_timeout=True,
                                resources={"metadata": {"path": str(workspace)}},
                                on_cell_start=before, on_cell_executed=after)
        try:
            client.execute(cleanup_kc=True)
            nb.metadata["cowork_execution"]["status"] = "completed"
        except Exception as exc:
            nb.metadata["cowork_execution"].update(status="failed", error_type=type(exc).__name__)
            print(json.dumps({"stage": "failed", **nb.metadata["cowork_execution"]}), flush=True)
            raise
        finally:
            save()
    # Keep image output inspectable with the existing view_image tool.
    figures = []
    for index, cell in enumerate(nb.cells, 1):
        for number, result in enumerate(cell.get("outputs", []), 1):
            data = result.get("data", {}).get("image/png")
            if data:
                destination = output.with_suffix("") / f"cell-{index}-{number}.png"
                destination.parent.mkdir(exist_ok=True)
                destination.write_bytes(base64.b64decode(data))
                figures.append(str(destination))
    return {"ok": True, "output": str(output), "figures": figures, "cells": len(nb.cells)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("output")
    parser.add_argument("--cell-timeout", type=int, default=120)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        workspace = os.environ.get("COWORK_WORKSPACE_DIR")
        if not workspace:
            raise ValueError("Current workspace is required.")
        result = execute(args.source, args.output, workspace=workspace,
                         cell_timeout=args.cell_timeout, overwrite=args.overwrite)
    except Exception as exc:
        print(json.dumps({"ok": False, "error_type": type(exc).__name__,
                          "message": "Execution failed; inspect the retained notebook and cell status."}), flush=True)
        return 1
    print(json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
