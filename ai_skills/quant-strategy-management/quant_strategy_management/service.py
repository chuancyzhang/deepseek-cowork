from __future__ import annotations

import html
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from .ai_commentary import generate_backtest_comment
from .backtest import BacktestEngine
from .dsl_parser import parse_strategy_prompt
from .models import StrategyDSL
from .paths import ensure_path
from .storage import StrategyStore


def _coerce_payload_dict(value: Any, field_name: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    raise ValueError(f"{field_name} must be an object")


def _store(root: str | Path | None = None) -> StrategyStore:
    data_root = ensure_path(root)
    return StrategyStore(data_root / "strategy.db")


def _artifact_root(root: str | Path | None = None) -> Path:
    data_root = ensure_path(root)
    artifact_root = data_root / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    return artifact_root


def _write_report(run_dir: str | Path, dsl: StrategyDSL, summary: dict[str, Any], comment: str) -> str:
    run_dir_path = Path(run_dir)
    report_path = run_dir_path / "report.html"
    html_content = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>{html.escape(dsl.strategy_name)} 回测报告</title>
  <style>
    body {{ font-family: 'Segoe UI', 'Microsoft YaHei UI', sans-serif; margin: 32px; color: #1d1d1f; background: #f6f8fb; }}
    main {{ max-width: 920px; margin: 0 auto; background: #ffffff; border: 1px solid #dde3ec; border-radius: 18px; padding: 28px; }}
    h1, h2 {{ margin: 0 0 14px; }}
    p {{ line-height: 1.6; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
    th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #eef0f4; }}
    th {{ width: 180px; color: #636366; font-weight: 600; }}
    .note {{ color: #636366; }}
  </style>
</head>
<body>
  <main>
    <h1>{html.escape(dsl.strategy_name)} 回测报告</h1>
    <p class="note">本报告仅供研究参考，不构成投资建议或收益承诺。</p>
    <h2>策略摘要</h2>
    <table>
      <tbody>
        <tr><th>标的</th><td>{html.escape(", ".join(dsl.symbols))}</td></tr>
        <tr><th>回测区间</th><td>{html.escape(summary["start"])} 至 {html.escape(summary["end"])}</td></tr>
        <tr><th>累计收益</th><td>{summary["total_return"] * 100:.2f}%</td></tr>
        <tr><th>年化收益</th><td>{summary["annual_return"] * 100:.2f}%</td></tr>
        <tr><th>最大回撤</th><td>{summary["max_drawdown"] * 100:.2f}%</td></tr>
        <tr><th>交易次数</th><td>{summary["trade_count"]}</td></tr>
      </tbody>
    </table>
    <h2>研究评论</h2>
    <p>{html.escape(comment).replace(chr(10), '<br/>')}</p>
  </main>
</body>
</html>
"""
    report_path.write_text(html_content, encoding="utf-8")
    return str(report_path)


def parse_quant_strategy_payload(prompt: str) -> dict[str, Any]:
    dsl = parse_strategy_prompt(prompt)
    return {"ok": True, "dsl": dsl.to_dict()}


def save_quant_strategy_payload(raw_prompt: str, dsl: dict[str, Any] | str, data_root: str | Path | None = None) -> dict[str, Any]:
    payload = _coerce_payload_dict(dsl, "dsl")
    compiled = StrategyDSL.from_dict(payload)
    store = _store(data_root)
    strategy_id, version_id = store.create_strategy_from_payload(raw_prompt, payload, compiled)
    return {
        "ok": True,
        "strategy_id": strategy_id,
        "strategy_version_id": version_id,
        "dsl": compiled.to_dict()
    }


def list_quant_strategies_payload(limit: int = 50, data_root: str | Path | None = None) -> dict[str, Any]:
    rows = _store(data_root).list_strategies(limit=max(1, int(limit or 50)))
    return {"ok": True, "strategies": rows}


def get_quant_strategy_payload(strategy_id: int, data_root: str | Path | None = None) -> dict[str, Any]:
    payload = _store(data_root).get_strategy(int(strategy_id))
    return {"ok": True, "strategy": payload}


def run_quant_backtest_payload(
    strategy_id: int | None = None,
    dsl: dict[str, Any] | str | None = None,
    raw_prompt: str = "",
    source_preference: str = "akshare",
    persist: bool = True,
    data_root: str | Path | None = None,
    market_data_loader: Callable[[str, StrategyDSL, str], pd.DataFrame] | None = None,
) -> dict[str, Any]:
    store = _store(data_root)
    if strategy_id is not None:
        compiled = store.load_latest_dsl(int(strategy_id))
        loaded = store.get_strategy(int(strategy_id))
        strategy_version_id = int(loaded["strategy_version_id"])
        resolved_strategy_id = int(strategy_id)
        prompt_text = loaded["raw_prompt"]
    elif dsl is not None:
        payload = _coerce_payload_dict(dsl, "dsl")
        compiled = StrategyDSL.from_dict(payload)
        prompt_text = raw_prompt
        resolved_strategy_id = None
        strategy_version_id = None
        if persist:
            resolved_strategy_id, strategy_version_id = store.create_strategy_from_payload(prompt_text, payload, compiled)
    else:
        raise ValueError("strategy_id or dsl is required")

    engine = BacktestEngine(_artifact_root(data_root) / "backtests", market_data_loader=market_data_loader)
    result = engine.run(compiled, source_preference=source_preference)
    summary = asdict(result["summary"])
    comment = generate_backtest_comment(compiled, result["summary"], result.get("data_warnings", {}))
    report_path = _write_report(result["run_dir"], compiled, summary, comment)
    backtest_id = None
    if persist and resolved_strategy_id is not None and strategy_version_id is not None:
        backtest_id = store.save_backtest(
            strategy_id=resolved_strategy_id,
            strategy_version_id=strategy_version_id,
            summary=summary,
            equity_curve_path=result["equity_curve_path"],
            trades_path=result["trades_path"],
            report_path=report_path,
            ai_comment=comment
        )
    return {
        "ok": True,
        "strategy_id": resolved_strategy_id,
        "strategy_version_id": strategy_version_id,
        "backtest_id": backtest_id,
        "summary": summary,
        "dsl": compiled.to_dict(),
        "ai_comment": comment,
        "artifact_paths": {
            "equity_curve": result["equity_curve_path"],
            "trades": result["trades_path"],
            "summary": result["summary_path"],
            "report": report_path
        },
        "data_warnings": result.get("data_warnings", {}),
        "raw_prompt": prompt_text
    }


def run_quant_strategy_from_prompt_payload(
    prompt: str,
    source_preference: str = "akshare",
    persist: bool = True,
    data_root: str | Path | None = None,
    market_data_loader: Callable[[str, StrategyDSL, str], pd.DataFrame] | None = None,
) -> dict[str, Any]:
    parsed = parse_quant_strategy_payload(prompt)
    return run_quant_backtest_payload(
        dsl=parsed["dsl"],
        raw_prompt=prompt,
        source_preference=source_preference,
        persist=persist,
        data_root=data_root,
        market_data_loader=market_data_loader
    )
