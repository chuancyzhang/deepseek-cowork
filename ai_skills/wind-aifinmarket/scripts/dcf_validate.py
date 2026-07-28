import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_support import emit, run_child

if len(sys.argv) == 1 or any(arg in {"-h", "--help"} for arg in sys.argv[1:]):
    emit("submit", "dcf_validate")
    emit("start", "dcf_validate")
    emit("run", "dcf_validate")
    print("Usage: dcf_validate.py <workspace-relative-excel-file> [workspace-relative-output.json]")
    print("Validates formula errors, terminal growth versus WACC, WACC range, and terminal value proportion.")
    emit("finish", "dcf_validate")
else:
    run_child("dcf_validate", "skills/dcf-model/scripts/validate_dcf.py")
