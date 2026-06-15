from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


Market = Literal["stock", "futures"]
Direction = Literal["long_only", "short_only", "long_short"]
TECHNICAL_INDICATOR_TYPES = {"moving_average", "rsi", "rolling_high", "rolling_low"}
RULE_TYPES = {"cross_over", "cross_under", "less_than", "greater_than", "breakout", "breakdown"}
RISK_RULE_TYPES = {"stop_loss", "take_profit", "trailing_stop", "max_holding_bars", "max_daily_trades"}
RULE_TYPE_ALIASES = {
    "rebalance": {"type": "greater_than", "left": "close", "right": 0, "action": "buy", "lookback": None},
    "buy_and_hold": {"type": "greater_than", "left": "close", "right": 0, "action": "buy", "lookback": None}
}
EXIT_RULE_TYPE_ALIASES = {
    "rebalance": {"type": "less_than", "left": "close", "right": 0, "action": "sell", "lookback": None},
    "buy_and_hold": {"type": "less_than", "left": "close", "right": 0, "action": "sell", "lookback": None}
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class IndicatorSpec:
    name: str
    type: str
    window: int | None = None
    source: str = "close"
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuleSpec:
    type: str
    left: str
    right: str | float | int | None = None
    action: str = ""
    lookback: int | None = None


@dataclass(frozen=True)
class RiskRuleSpec:
    type: str
    value: float


def _known_fields(cls, payload: dict[str, Any]) -> dict[str, Any]:
    field_names = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
    return {key: value for key, value in payload.items() if key in field_names}


def _indicator_from_payload(item: dict[str, Any]) -> IndicatorSpec:
    payload = _known_fields(IndicatorSpec, item)
    payload.setdefault("params", {})
    return IndicatorSpec(**payload)


def _rule_from_payload(item: dict[str, Any], group: str) -> RuleSpec:
    payload = _known_fields(RuleSpec, item)
    aliases = EXIT_RULE_TYPE_ALIASES if group == "exit_rules" else RULE_TYPE_ALIASES
    rule_type = str(payload.get("type", item.get("type", ""))).strip()
    if rule_type in aliases:
        normalized = dict(aliases[rule_type])
        normalized["action"] = str(payload.get("action") or normalized["action"])
        return RuleSpec(**normalized)
    return RuleSpec(**payload)


def _risk_rule_from_payload(item: dict[str, Any]) -> RiskRuleSpec:
    payload = _known_fields(RiskRuleSpec, item)
    if "value" not in payload and isinstance(item.get("params"), dict):
        for key in ("value", "threshold", "ratio", "stop_loss"):
            if key in item["params"]:
                payload["value"] = item["params"][key]
                break
    return RiskRuleSpec(type=str(payload.get("type", item.get("type", ""))), value=float(payload.get("value", 0.0)))


@dataclass(frozen=True)
class StrategyDSL:
    strategy_name: str
    market: Market
    symbols: list[str]
    frequency: str
    start_date: str
    end_date: str
    initial_cash: float
    indicators: list[IndicatorSpec]
    entry_rules: list[RuleSpec]
    exit_rules: list[RuleSpec]
    risk_rules: list[RiskRuleSpec] = field(default_factory=list)
    position_sizing: dict[str, Any] = field(default_factory=lambda: {"type": "fixed_ratio", "value": 1.0})
    cost_model: dict[str, Any] = field(default_factory=lambda: {"commission_rate": 0.0003, "slippage": 0.0})
    engine: dict[str, Any] = field(default_factory=lambda: {"mode": "backtest", "backend_hint": "auto"})
    risk_limits: dict[str, Any] = field(default_factory=lambda: {"max_position_ratio": 1.0})
    direction: Direction = "long_only"

    def validate(self) -> None:
        if self.market not in {"stock", "futures"}:
            raise ValueError("market must be stock or futures")
        if not self.symbols:
            raise ValueError("symbols cannot be empty")
        if self.frequency != "1d":
            raise ValueError("frequency must be 1d")
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if not self.entry_rules:
            raise ValueError("at least one entry rule is required")
        if not self.exit_rules:
            raise ValueError("at least one exit rule is required")
        if self.direction not in {"long_only", "short_only", "long_short"}:
            raise ValueError("direction is not supported")
        for index, indicator in enumerate(self.indicators):
            if indicator.type not in TECHNICAL_INDICATOR_TYPES:
                raise ValueError(f"unsupported indicator type at indicators[{index}].type: {indicator.type!r}")
            if indicator.window is not None and int(indicator.window) <= 0:
                raise ValueError(f"indicators[{index}].window must be positive")
        for group_name, rules in (("entry_rules", self.entry_rules), ("exit_rules", self.exit_rules)):
            for index, rule in enumerate(rules):
                if rule.type not in RULE_TYPES:
                    raise ValueError(f"unsupported rule type at {group_name}[{index}].type: {rule.type!r}")
        for index, rule in enumerate(self.risk_rules):
            if rule.type not in RISK_RULE_TYPES:
                raise ValueError(f"unsupported risk rule type at risk_rules[{index}].type: {rule.type!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_name": self.strategy_name,
            "market": self.market,
            "symbols": self.symbols,
            "frequency": self.frequency,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "initial_cash": self.initial_cash,
            "indicators": [item.__dict__ for item in self.indicators],
            "entry_rules": [item.__dict__ for item in self.entry_rules],
            "exit_rules": [item.__dict__ for item in self.exit_rules],
            "risk_rules": [item.__dict__ for item in self.risk_rules],
            "position_sizing": self.position_sizing,
            "cost_model": self.cost_model,
            "engine": self.engine,
            "risk_limits": self.risk_limits,
            "direction": self.direction
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "StrategyDSL":
        dsl = cls(
            strategy_name=str(payload["strategy_name"]),
            market=str(payload["market"]),  # type: ignore[arg-type]
            symbols=list(payload["symbols"]),
            frequency=str(payload.get("frequency", "1d")),
            start_date=str(payload["start_date"]),
            end_date=str(payload["end_date"]),
            initial_cash=float(payload.get("initial_cash", 1_000_000)),
            indicators=[_indicator_from_payload(item) for item in payload.get("indicators", [])],
            entry_rules=[_rule_from_payload(item, "entry_rules") for item in payload.get("entry_rules", [])],
            exit_rules=[_rule_from_payload(item, "exit_rules") for item in payload.get("exit_rules", [])],
            risk_rules=[_risk_rule_from_payload(item) for item in payload.get("risk_rules", [])],
            position_sizing=payload.get("position_sizing", {"type": "fixed_ratio", "value": 1.0}),
            cost_model=payload.get("cost_model", {"commission_rate": 0.0003, "slippage": 0.0}),
            engine=payload.get("engine", {"mode": "backtest", "backend_hint": "auto"}),
            risk_limits=payload.get("risk_limits", {"max_position_ratio": 1.0}),
            direction=payload.get("direction", "long_only")
        )
        dsl.validate()
        return dsl


@dataclass(frozen=True)
class BacktestSummary:
    start: str
    end: str
    initial_cash: float
    final_equity: float
    total_return: float
    annual_return: float
    annual_volatility: float
    sharpe_simple: float | None
    max_drawdown: float
    trade_count: int
    win_rate: float | None
    profit_loss_ratio: float | None
