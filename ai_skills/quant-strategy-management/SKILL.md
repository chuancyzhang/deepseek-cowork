---
name: quant-strategy-management
description: Manage standalone quantitative strategy assets with controlled Strategy DSL parsing, storage, daily backtests, and report artifacts.
description_cn: 独立管理量化策略资产，支持受控 Strategy DSL 解析、持久化、日线回测和报告产物。
license: Apache-2.0
metadata:
  author: deepseek-cowork team
  version: "1.0"
allowed-tools: [parse_quant_strategy, save_quant_strategy, list_quant_strategies, get_quant_strategy, run_quant_backtest, run_quant_strategy_from_prompt]
---

# Quant Strategy Management

This skill provides a standalone quantitative strategy workflow inside Cowork. It is designed as a self-contained skill package and does not depend on `D:\code\测试策略` at runtime.

## Capabilities

1. Parse Chinese strategy ideas into a controlled `Strategy DSL`.
2. Save strategies and versioned DSL snapshots in a local SQLite asset store.
3. Run daily backtests and persist summary, trades, equity curve, and HTML report artifacts.
4. Expose the same workflow through Cowork tools and Python script entrypoints.

## Usage Guidelines

- Use `parse_quant_strategy` when the user is still shaping a strategy idea.
- Use `save_quant_strategy` after a DSL has been confirmed and should become a reusable asset.
- Use `run_quant_backtest` when the strategy already exists or when a DSL is ready to execute.
- Use `run_quant_strategy_from_prompt` for a one-shot parse, save, and backtest flow.
- Treat all outputs as research support only. Do not present them as guaranteed investment outcomes.

## Safety Boundaries

- This skill does not connect to broker accounts.
- This skill does not place real orders.
- This skill does not produce guaranteed-profit language.
- Default scope is daily research and backtest analysis only.

## Script Entry Notes

- The CLI entrypoint is `scripts/cli.py`.
- Script mode mirrors the tool surface so the skill stays independently executable inside the Cowork skill runtime.

## Current Runtime Notes

- Data defaults to `akshare`; `yfinance` is supported as an optional fallback path.
- Skill data is stored under the skill-specific app data root instead of the workspace tree.
- Generated reports and CSV artifacts are safe to reference as local research outputs, not trading instructions.
