from __future__ import annotations

import re
from datetime import date

from .models import IndicatorSpec, RiskRuleSpec, RuleSpec, StrategyDSL


def _extract_ints(text: str) -> list[int]:
    return [int(item) for item in re.findall(r"(\d+)\s*(?:日|day|days|d)?", text, flags=re.I)]


def _extract_symbols(text: str, market: str) -> list[str]:
    explicit = re.findall(r"\b(?:sh|sz)?\d{6}(?:\.(?:SH|SZ))?\b|\b[A-Za-z]{1,3}_main\b|\b[A-Za-z]{1,3}\d{3,4}\b", text)
    if explicit:
        return [item.upper() if "." in item else item for item in explicit]
    if "沪深300" in text or "510300" in text:
        return ["510300.SH"]
    if "上证50" in text or "510050" in text:
        return ["510050.SH"]
    if "螺纹" in text or "rb" in text.lower():
        return ["rb_main"]
    if market == "futures":
        return ["rb_main"]
    return ["510300.SH"]


def _extract_year_range(text: str) -> tuple[str, str]:
    years = [int(y) for y in re.findall(r"(20\d{2})", text)]
    if len(years) >= 2:
        return f"{min(years)}-01-01", f"{max(years)}-12-31"
    if len(years) == 1:
        return f"{years[0]}-01-01", date.today().isoformat()
    return "2021-01-01", date.today().isoformat()


def _extract_cash(text: str) -> float:
    match = re.search(r"(\d+(?:\.\d+)?)\s*万", text)
    if match:
        return float(match.group(1)) * 10_000
    match = re.search(r"initial[_ ]?cash\s*[:=]\s*(\d+(?:\.\d+)?)", text, flags=re.I)
    if match:
        return float(match.group(1))
    return 1_000_000.0


def parse_strategy_prompt(text: str) -> StrategyDSL:
    normalized = text.strip()
    if not normalized:
        raise ValueError("prompt is required")
    market = "futures" if any(word in normalized for word in ("期货", "合约", "主力", "螺纹", "豆粕", "沪铜")) else "stock"
    symbols = _extract_symbols(normalized, market)
    start_date, end_date = _extract_year_range(normalized)
    cash = _extract_cash(normalized)
    ints = _extract_ints(normalized)

    commission_rate = 0.0003
    fee_match = re.search(r"万分之\s*(\d+(?:\.\d+)?)", normalized)
    if fee_match:
        commission_rate = float(fee_match.group(1)) / 10_000
    stop_loss = 0.0
    stop_match = re.search(r"(?:止损|亏损)\s*(\d+(?:\.\d+)?)\s*%", normalized)
    if stop_match:
        stop_loss = float(stop_match.group(1)) / 100

    if "RSI" in normalized.upper():
        period = ints[0] if ints else 14
        low = 30
        high = 70
        if len(ints) >= 3:
            low, high = ints[1], ints[2]
        indicators = [IndicatorSpec(name="rsi", type="rsi", window=period)]
        entry = [RuleSpec(type="less_than", left="rsi", right=low, action="buy")]
        exit_ = [RuleSpec(type="greater_than", left="rsi", right=high, action="sell")]
        name = "RSI动量策略"
    elif "突破" in normalized:
        lookback = ints[0] if ints else 20
        indicators = [IndicatorSpec(name=f"high_{lookback}", type="rolling_high", window=lookback)]
        entry = [RuleSpec(type="breakout", left="close", right=f"high_{lookback}", action="buy", lookback=lookback)]
        exit_ = [RuleSpec(type="breakdown", left="close", right=f"high_{lookback}", action="sell", lookback=lookback)]
        name = "突破跟随策略"
    else:
        windows = sorted(set([item for item in ints if item >= 2]))[:2]
        if len(windows) < 2:
            windows = [5, 20]
        short, long = windows[0], windows[1]
        indicators = [
            IndicatorSpec(name=f"ma_{short}", type="moving_average", window=short),
            IndicatorSpec(name=f"ma_{long}", type="moving_average", window=long)
        ]
        entry = [RuleSpec(type="cross_over", left=f"ma_{short}", right=f"ma_{long}", action="buy")]
        exit_ = [RuleSpec(type="cross_under", left=f"ma_{short}", right=f"ma_{long}", action="sell")]
        name = "双均线趋势策略"

    risk_rules = [RiskRuleSpec(type="stop_loss", value=stop_loss)] if stop_loss else []
    dsl = StrategyDSL(
        strategy_name=name,
        market=market,  # type: ignore[arg-type]
        symbols=symbols,
        frequency="1d",
        start_date=start_date,
        end_date=end_date,
        initial_cash=cash,
        indicators=indicators,
        entry_rules=entry,
        exit_rules=exit_,
        risk_rules=risk_rules,
        position_sizing={"type": "fixed_ratio", "value": 1.0 if market == "stock" else 0.2},
        cost_model={"commission_rate": commission_rate, "slippage": 0.0001, "stamp_tax_rate": 0.0005},
        risk_limits={"max_position_ratio": 1.0 if market == "stock" else 0.3}
    )
    dsl.validate()
    return dsl
