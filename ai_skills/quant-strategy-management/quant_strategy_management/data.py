from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Callable

import pandas as pd

from .models import StrategyDSL


STANDARD_COLUMNS = ["open", "high", "low", "close", "volume", "amount", "open_interest"]


class DataSourceError(RuntimeError):
    pass


def bare_symbol(symbol: str) -> str:
    raw = clean_symbol(symbol).split(".")[0]
    return raw[2:] if raw.lower().startswith(("sh", "sz")) else raw


def clean_symbol(symbol: str) -> str:
    return str(symbol).strip()


def is_china_index_symbol(symbol: str) -> bool:
    normalized = clean_symbol(symbol).upper()
    raw = bare_symbol(normalized)
    suffix = normalized.split(".")[1] if "." in normalized else ""
    if normalized.startswith(("SH000", "SZ399", "CSI", "CNI")):
        return True
    if suffix in {"CSI", "CNI"}:
        return True
    if suffix == "SH" and raw.startswith(("000", "930", "931", "932")):
        return True
    if suffix == "SZ" and raw.startswith("399"):
        return True
    if not suffix and raw.startswith(("000", "399", "930", "931", "932")):
        return True
    return False


def market_symbol(symbol: str) -> str:
    raw = symbol.split(".")[0].lower()
    if raw.startswith(("sh", "sz")):
        return raw
    if re.fullmatch(r"\d{6}", raw):
        return f"sh{raw}" if raw.startswith(("5", "6", "9")) else f"sz{raw}"
    return raw


def pick_column(columns, candidates: tuple[str, ...]) -> str:
    normalized = {str(column).strip(): column for column in columns}
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    raise KeyError(f"Could not find any of columns: {', '.join(candidates)}")


def optional_column(columns, candidates: tuple[str, ...]) -> str | None:
    normalized = {str(column).strip(): column for column in columns}
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    return None


def normalize_market_bars(history: pd.DataFrame, symbol: str, provider: str, source_symbol: str, adjust: str = "qfq") -> pd.DataFrame:
    if history.empty:
        return pd.DataFrame(columns=["datetime", "symbol", *STANDARD_COLUMNS])
    date_col = pick_column(history.columns, ("日期", "date", "datetime", "时间", "Date"))
    open_col = pick_column(history.columns, ("开盘", "open", "Open"))
    high_col = pick_column(history.columns, ("最高", "high", "High"))
    low_col = pick_column(history.columns, ("最低", "low", "Low"))
    close_col = pick_column(history.columns, ("收盘", "close", "Close"))
    volume_col = optional_column(history.columns, ("成交量", "volume", "vol", "Volume")) or close_col
    amount_col = optional_column(history.columns, ("成交额", "amount", "turnover", "Amount"))
    oi_col = optional_column(history.columns, ("持仓量", "open_interest", "hold"))
    frame = pd.DataFrame(
        {
            "datetime": pd.to_datetime(history[date_col]),
            "symbol": symbol,
            "open": pd.to_numeric(history[open_col], errors="coerce"),
            "high": pd.to_numeric(history[high_col], errors="coerce"),
            "low": pd.to_numeric(history[low_col], errors="coerce"),
            "close": pd.to_numeric(history[close_col], errors="coerce"),
            "volume": pd.to_numeric(history[volume_col], errors="coerce"),
            "amount": pd.to_numeric(history[amount_col], errors="coerce") if amount_col else 0.0,
            "open_interest": pd.to_numeric(history[oi_col], errors="coerce") if oi_col else 0.0,
            "provider": provider,
            "source_symbol": source_symbol,
            "adjust": adjust
        }
    )
    return frame.sort_values("datetime").dropna(subset=["close"])


class MarketDataProvider:
    def __init__(self, loader: Callable[[str, StrategyDSL, str], pd.DataFrame] | None = None) -> None:
        self.loader = loader

    def load_bars(self, dsl: StrategyDSL, source_preference: str = "akshare") -> dict[str, pd.DataFrame]:
        data: dict[str, pd.DataFrame] = {}
        for symbol in dsl.symbols:
            if self.loader:
                bars = self.loader(symbol, dsl, source_preference)
            elif source_preference == "yfinance":
                bars = self._fetch_yfinance(symbol, dsl)
            else:
                bars = self._fetch_akshare(symbol, dsl)
            if bars.empty:
                raise DataSourceError(f"no bars returned for {symbol}")
            data[symbol] = bars
        return data

    def _fetch_akshare(self, symbol: str, dsl: StrategyDSL) -> pd.DataFrame:
        import akshare as ak  # type: ignore

        raw_symbol = bare_symbol(symbol)
        clean_start = dsl.start_date.replace("-", "")
        clean_end = dsl.end_date.replace("-", "")
        if str(symbol).endswith("_main"):
            history = ak.futures_main_sina(symbol=raw_symbol.replace("_main", "").upper(), start_date=clean_start, end_date=clean_end)
        elif is_china_index_symbol(symbol) and hasattr(ak, "index_zh_a_hist"):
            history = ak.index_zh_a_hist(symbol=raw_symbol, period="daily", start_date=clean_start, end_date=clean_end)
        elif raw_symbol.startswith(("5", "1")) and hasattr(ak, "fund_etf_hist_em"):
            history = ak.fund_etf_hist_em(symbol=raw_symbol, period="daily", start_date=clean_start, end_date=clean_end, adjust="qfq")
        elif hasattr(ak, "stock_zh_a_hist"):
            history = ak.stock_zh_a_hist(symbol=raw_symbol, period="daily", start_date=clean_start, end_date=clean_end, adjust="qfq")
        else:
            history = ak.stock_zh_a_daily(symbol=market_symbol(raw_symbol), start_date=clean_start, end_date=clean_end, adjust="qfq")
        frame = normalize_market_bars(history, symbol, "akshare", raw_symbol, "qfq")
        return self._bars_from_frame(frame)

    def _fetch_yfinance(self, symbol: str, dsl: StrategyDSL) -> pd.DataFrame:
        import yfinance as yf  # type: ignore

        exclusive_end = (datetime.fromisoformat(dsl.end_date) + timedelta(days=1)).date().isoformat()
        history = yf.download(
            symbol,
            start=dsl.start_date,
            end=exclusive_end,
            interval="1d",
            auto_adjust=True,
            progress=False,
            multi_level_index=False
        )
        if isinstance(history.columns, pd.MultiIndex):
            history.columns = history.columns.get_level_values(0)
        history = history.reset_index()
        history.columns = [column[0] if isinstance(column, tuple) else column for column in history.columns]
        frame = normalize_market_bars(history, symbol, "yfinance", symbol, "qfq")
        return self._bars_from_frame(frame)

    def _bars_from_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return pd.DataFrame(columns=STANDARD_COLUMNS)
        frame = frame.set_index("datetime").sort_index()
        bars = frame[[column for column in STANDARD_COLUMNS if column in frame.columns]].copy()
        for column in STANDARD_COLUMNS:
            if column not in bars.columns:
                bars[column] = 0.0
        return bars[STANDARD_COLUMNS]
