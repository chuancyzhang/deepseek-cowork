from __future__ import annotations

import pandas as pd

from .models import IndicatorSpec


def calculate_rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    avg_gain = gains.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = losses.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    return (100 - (100 / (1 + rs))).fillna(50.0)


def apply_indicators(bars: pd.DataFrame, indicators: list[IndicatorSpec]) -> pd.DataFrame:
    frame = bars.copy()
    for spec in indicators:
        source = spec.source or "close"
        if source not in frame.columns:
            raise ValueError(f"indicator source {source!r} does not exist")
        if spec.type == "moving_average":
            if not spec.window:
                raise ValueError(f"{spec.name} requires window")
            frame[spec.name] = frame[source].rolling(spec.window, min_periods=spec.window).mean()
        elif spec.type == "rsi":
            if not spec.window:
                raise ValueError(f"{spec.name} requires window")
            frame[spec.name] = calculate_rsi(frame[source], spec.window)
        elif spec.type == "rolling_high":
            if not spec.window:
                raise ValueError(f"{spec.name} requires window")
            frame[spec.name] = frame[source].rolling(spec.window, min_periods=spec.window).max().shift(1)
        elif spec.type == "rolling_low":
            if not spec.window:
                raise ValueError(f"{spec.name} requires window")
            frame[spec.name] = frame[source].rolling(spec.window, min_periods=spec.window).min().shift(1)
        else:
            raise ValueError(f"{spec.name}: unsupported technical indicator type {spec.type!r}")
    return frame
