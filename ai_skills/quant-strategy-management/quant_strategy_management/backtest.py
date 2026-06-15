from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from .data import MarketDataProvider
from .indicators import apply_indicators
from .models import BacktestSummary, RuleSpec, StrategyDSL, utc_now_iso


def _value(frame: pd.DataFrame, index: int, key: str | float | int | None) -> float:
    if key is None:
        return float("nan")
    if isinstance(key, (int, float)):
        return float(key)
    if key not in frame.columns:
        raise ValueError(f"rule field {key!r} does not exist")
    return float(frame.iloc[index][key])


def rule_triggered(frame: pd.DataFrame, index: int, rule: RuleSpec) -> bool:
    if index <= 0:
        return False
    left_now = _value(frame, index, rule.left)
    right_now = _value(frame, index, rule.right)
    left_prev = _value(frame, index - 1, rule.left)
    right_prev = _value(frame, index - 1, rule.right)
    if pd.isna(left_now) or pd.isna(right_now):
        return False
    if rule.type == "cross_over":
        return left_prev <= right_prev and left_now > right_now
    if rule.type == "cross_under":
        return left_prev >= right_prev and left_now < right_now
    if rule.type in {"less_than", "breakdown"}:
        return left_now < right_now
    if rule.type in {"greater_than", "breakout"}:
        return left_now > right_now
    raise ValueError(f"unsupported rule type: {rule.type}")


def _max_drawdown(equity: pd.Series) -> pd.Series:
    return equity / equity.cummax() - 1


class BacktestEngine:
    def __init__(self, output_dir: str | Path, market_data_loader: Callable[[str, StrategyDSL, str], pd.DataFrame] | None = None) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.data_provider = MarketDataProvider(loader=market_data_loader)

    def run(self, dsl: StrategyDSL, source_preference: str = "akshare") -> dict[str, Any]:
        dsl.validate()
        raw_data = self.data_provider.load_bars(dsl, source_preference=source_preference)
        prepared = {symbol: apply_indicators(bars, dsl.indicators) for symbol, bars in raw_data.items()}
        equity_curve, trades = self._simulate(prepared, dsl)
        summary = summarize_backtest(equity_curve, trades, dsl)
        run_id = utc_now_iso().replace(":", "").replace("+", "_")
        run_dir = self.output_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        equity_path = run_dir / "equity_curve.csv"
        trades_path = run_dir / "trades.csv"
        summary_path = run_dir / "summary.json"
        equity_curve.to_csv(equity_path, encoding="utf-8-sig")
        trades.to_csv(trades_path, index=False, encoding="utf-8-sig")
        summary_path.write_text(json.dumps(asdict(summary), ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "run_id": run_id,
            "summary": summary,
            "equity_curve": equity_curve,
            "trades": trades,
            "equity_curve_path": str(equity_path),
            "trades_path": str(trades_path),
            "summary_path": str(summary_path),
            "data_warnings": {},
            "run_dir": str(run_dir)
        }

    def _simulate(self, data: dict[str, pd.DataFrame], dsl: StrategyDSL) -> tuple[pd.DataFrame, pd.DataFrame]:
        dates = sorted(set().union(*[set(frame.index) for frame in data.values()]))
        cash = float(dsl.initial_cash)
        positions = {symbol: 0.0 for symbol in data}
        entry_prices = {symbol: 0.0 for symbol in data}
        commission = float(dsl.cost_model.get("commission_rate", 0.0003))
        stamp_tax = float(dsl.cost_model.get("stamp_tax_rate", 0.0005 if dsl.market == "stock" else 0.0))
        slippage = float(dsl.cost_model.get("slippage", 0.0))
        target_ratio = float(dsl.position_sizing.get("value", 1.0))
        max_ratio = float(dsl.risk_limits.get("max_position_ratio", target_ratio))
        target_ratio = min(target_ratio, max_ratio)
        stop_loss = next((rule.value for rule in dsl.risk_rules if rule.type == "stop_loss"), 0.0)
        equity_rows: list[dict[str, Any]] = []
        trade_rows: list[dict[str, Any]] = []

        for current_date in dates:
            prices = {
                symbol: float(frame.loc[current_date, "close"])
                for symbol, frame in data.items()
                if current_date in frame.index and not pd.isna(frame.loc[current_date, "close"])
            }
            if not prices:
                continue
            for symbol, frame in data.items():
                if current_date not in frame.index:
                    continue
                idx = frame.index.get_loc(current_date)
                price = float(frame.loc[current_date, "close"])
                exit_signal = any(rule_triggered(frame, idx, rule) for rule in dsl.exit_rules)
                stopped = bool(stop_loss and positions[symbol] > 0 and price <= entry_prices[symbol] * (1 - stop_loss))
                if positions[symbol] > 0 and (exit_signal or stopped):
                    trade_price = price * (1 - slippage)
                    qty = positions[symbol]
                    gross = qty * trade_price
                    fee = gross * (commission + stamp_tax)
                    cash += gross - fee
                    positions[symbol] = 0.0
                    trade_rows.append(
                        {
                            "date": current_date,
                            "symbol": symbol,
                            "side": "SELL",
                            "price": trade_price,
                            "quantity": qty,
                            "amount": gross,
                            "fee": fee,
                            "reason": "stop_loss" if stopped else "exit_rule"
                        }
                    )

            market_value = sum(positions[symbol] * prices.get(symbol, 0.0) for symbol in positions)
            equity_before = cash + market_value
            for symbol, frame in data.items():
                if current_date not in frame.index or positions[symbol] > 0:
                    continue
                idx = frame.index.get_loc(current_date)
                if not any(rule_triggered(frame, idx, rule) for rule in dsl.entry_rules):
                    continue
                price = float(frame.loc[current_date, "close"])
                trade_price = price * (1 + slippage)
                allowed_slots = max(len(data), 1)
                target_value = equity_before * target_ratio / allowed_slots
                quantity = int((target_value / trade_price) // (100 if dsl.market == "stock" else 1))
                if dsl.market == "stock":
                    quantity = int(quantity * 100)
                if quantity <= 0:
                    continue
                gross = quantity * trade_price
                fee = gross * commission
                if gross + fee > cash:
                    quantity = int((cash / (trade_price * (1 + commission))) // (100 if dsl.market == "stock" else 1))
                    if dsl.market == "stock":
                        quantity = int(quantity * 100)
                    gross = quantity * trade_price
                    fee = gross * commission
                if quantity <= 0:
                    continue
                cash -= gross + fee
                positions[symbol] += quantity
                entry_prices[symbol] = trade_price
                trade_rows.append(
                    {
                        "date": current_date,
                        "symbol": symbol,
                        "side": "BUY",
                        "price": trade_price,
                        "quantity": quantity,
                        "amount": gross,
                        "fee": fee,
                        "reason": "entry_rule"
                    }
                )

            market_value = sum(positions[symbol] * prices.get(symbol, 0.0) for symbol in positions)
            equity_rows.append(
                {
                    "date": current_date,
                    "cash": cash,
                    "market_value": market_value,
                    "equity": cash + market_value,
                    "positions": sum(1 for qty in positions.values() if qty > 0)
                }
            )

        equity_curve = pd.DataFrame(equity_rows).set_index("date") if equity_rows else pd.DataFrame()
        trades = pd.DataFrame(trade_rows)
        return equity_curve, trades


def summarize_backtest(equity_curve: pd.DataFrame, trades: pd.DataFrame, dsl: StrategyDSL) -> BacktestSummary:
    if equity_curve.empty:
        raise RuntimeError("backtest produced empty equity curve")
    daily_return = equity_curve["equity"].pct_change().dropna()
    total_return = equity_curve["equity"].iloc[-1] / dsl.initial_cash - 1
    annual_return = (1 + total_return) ** (252 / max(len(equity_curve), 1)) - 1
    annual_vol = float(daily_return.std() * (252 ** 0.5)) if not daily_return.empty else 0.0
    sharpe = annual_return / annual_vol if annual_vol else None
    drawdown = _max_drawdown(equity_curve["equity"])
    win_rate = None
    pl_ratio = None
    if not trades.empty and {"side", "amount"}.issubset(trades.columns):
        sells = trades[trades["side"] == "SELL"]["amount"].astype(float).to_list()
        buys = trades[trades["side"] == "BUY"]["amount"].astype(float).to_list()
        paired = min(len(sells), len(buys))
        if paired:
            pnl = [sells[idx] - buys[idx] for idx in range(paired)]
            wins = [value for value in pnl if value > 0]
            losses = [-value for value in pnl if value < 0]
            win_rate = float(len(wins) / paired)
            if wins and losses:
                pl_ratio = float((sum(wins) / len(wins)) / (sum(losses) / len(losses)))
    return BacktestSummary(
        start=equity_curve.index.min().strftime("%Y-%m-%d"),
        end=equity_curve.index.max().strftime("%Y-%m-%d"),
        initial_cash=float(dsl.initial_cash),
        final_equity=float(equity_curve["equity"].iloc[-1]),
        total_return=float(total_return),
        annual_return=float(annual_return),
        annual_volatility=annual_vol,
        sharpe_simple=float(sharpe) if sharpe is not None else None,
        max_drawdown=float(drawdown.min()),
        trade_count=int(len(trades)),
        win_rate=win_rate,
        profit_loss_ratio=pl_ratio
    )
