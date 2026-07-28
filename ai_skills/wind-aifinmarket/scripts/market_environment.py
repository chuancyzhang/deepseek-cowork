import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_support import run_child

run_child("market_environment", "skills/market-environment-analysis/scripts/market_utils.py")
