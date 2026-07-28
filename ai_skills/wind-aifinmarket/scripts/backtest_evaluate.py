import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_support import run_child

run_child("backtest_evaluate", "skills/backtest-expert/scripts/evaluate_backtest.py")
