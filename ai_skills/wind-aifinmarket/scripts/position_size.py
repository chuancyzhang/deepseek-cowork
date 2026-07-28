import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_support import run_child

run_child("position_size", "skills/position-sizer/scripts/position_sizer.py")
