import importlib.util
from pathlib import Path
import subprocess
import sys

import nbformat
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/data-analysis/scripts/execute_notebook.py"
spec = importlib.util.spec_from_file_location("notebook_runner", SCRIPT)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def notebook(path, cells):
    nb = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(cell) for cell in cells])
    nbformat.write(nb, path)
    return path


def test_fresh_kernel_real_outputs_and_figures(tmp_path):
    source = notebook(tmp_path / "input.ipynb", ["amount = 6 * 7\nprint(amount)",
        "%matplotlib inline\nimport matplotlib.pyplot as plt\nplt.plot([1, 2], [3, 5]); plt.show()"])
    original = source.read_bytes()
    result = runner.execute(source, "output.ipynb", workspace=tmp_path)
    nb = nbformat.read(result["output"], as_version=4)
    assert nb.cells[0].outputs[0].text.strip() == "42"
    assert [cell.execution_count for cell in nb.cells] == [1, 2]
    assert nb.metadata.cowork_execution.status == "completed"
    from PIL import Image
    with Image.open(result["figures"][0]) as image:
        image.verify()
    assert source.read_bytes() == original
    with pytest.raises(ValueError, match="Output already exists"):
        runner.execute(source, "output.ipynb", workspace=tmp_path)
    # No hidden state from the first kernel can satisfy this notebook.
    notebook(source, ["print(amount)"])
    with pytest.raises(Exception, match="amount"):
        runner.execute(source, "fresh.ipynb", workspace=tmp_path)
    assert nbformat.read(tmp_path / "fresh.ipynb", 4).cells[0].outputs[0].ename == "NameError"


def test_cell_error_and_timeout_keep_completed_results(tmp_path):
    for last, error in [("raise ValueError('bad data')", "CellExecutionError"),
                        ("import time; time.sleep(30)", "CellTimeoutError")]:
        source = notebook(tmp_path / "input.ipynb", ["print('retained')", last, "print('must not run')"])
        output = tmp_path / f"{error}.ipynb"
        with pytest.raises(Exception):
            runner.execute(source, output, workspace=tmp_path, cell_timeout=1)
        nb = nbformat.read(output, 4)
        assert nb.cells[0].outputs[0].text == "retained\n"
        assert nb.metadata.cowork_execution.error_type == error
        assert nb.metadata.cowork_execution.cell == 2
        assert nb.cells[2].execution_count is None


def test_host_cancellation_stops_kernel_and_retains_checkpoint(tmp_path, monkeypatch):
    from core.sandbox_runtime import run_skill_script_in_sandbox
    from core.process_utils import subprocess_kwargs_no_window
    import core.sandbox_runtime as runtime
    marker = tmp_path / "second-cell-started"
    source = notebook(tmp_path / "input.ipynb", ["print('saved')",
        "from pathlib import Path\nPath('second-cell-started').touch()\nimport time; time.sleep(60)"])
    output = tmp_path / "cancelled.ipynb"
    # Use the real host polling/cancellation path with the test interpreter.
    monkeypatch.setattr(runtime, "build_skill_script_command", lambda *a, **k: ([sys.executable, str(SCRIPT), str(source), str(output)], "exec"))
    def start(command, **kwargs):
        import os
        return subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env={**os.environ, "COWORK_WORKSPACE_DIR": str(tmp_path)},
                                **subprocess_kwargs_no_window())
    monkeypatch.setattr(runtime, "run_in_sandbox", start)
    def cancel():
        # Poll a marker created after the second checkpoint. Opening the notebook
        # while its writer replaces it can block os.replace on Windows.
        return marker.exists()
    result = run_skill_script_in_sandbox("data-analysis", str(SCRIPT), "python", timeout_seconds=30, abort_check=cancel)
    assert result.get("status") == "cancelled" and result["aborted"], result
    nb = nbformat.read(output, 4)
    assert nb.cells[0].outputs[0].text == "saved\n"
    assert nb.cells[1].execution_count is None
