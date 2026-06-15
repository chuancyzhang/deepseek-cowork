from __future__ import annotations

from .models import BacktestSummary, StrategyDSL


def pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:.2f}%"


def number(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}"


def generate_backtest_comment(
    dsl: StrategyDSL,
    summary: BacktestSummary,
    data_warnings: dict[str, str] | None = None,
) -> str:
    warnings = data_warnings or {}
    strategy_type = "技术趋势/择时策略"
    if any(item.type == "rsi" for item in dsl.indicators):
        strategy_type = "均值回归型 RSI 策略"
    if any(rule.type in {"cross_over", "cross_under"} for rule in [*dsl.entry_rules, *dsl.exit_rules]):
        strategy_type = "趋势跟踪型均线策略"

    verdict = "可继续研究，但不建议直接实盘。"
    if summary.sharpe_simple is not None and summary.sharpe_simple > 1 and summary.max_drawdown > -0.2:
        verdict = "具备继续样本外验证价值。"
    elif summary.max_drawdown < -0.3:
        verdict = "回撤压力较大，需要先优化风控。"

    warning_text = ""
    if warnings:
        warning_text = "\n\n数据提示：" + "；".join(f"{symbol}: {text}" for symbol, text in warnings.items())

    return (
        f"策略类型：{strategy_type}。\n\n"
        f"回测区间 {summary.start} 至 {summary.end}，累计收益 {pct(summary.total_return)}，"
        f"年化收益 {pct(summary.annual_return)}，最大回撤 {pct(summary.max_drawdown)}，"
        f"年化波动 {pct(summary.annual_volatility)}，简化夏普 {number(summary.sharpe_simple)}，"
        f"交易次数 {summary.trade_count}。\n\n"
        f"研究判断：{verdict} 该结论仅基于当前策略规则、数据源和回测假设，不构成确定性投资建议。\n\n"
        "下一步建议：做参数敏感性测试、样本外验证、不同市场环境分段分析，并检查手续费、滑点和流动性假设对结果的影响。"
        f"{warning_text}"
    )
