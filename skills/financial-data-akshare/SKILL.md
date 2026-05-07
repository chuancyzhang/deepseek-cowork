---
name: financial-data-akshare
description: Query free financial market, fund, index, futures, bond, macroeconomic, and company data through AKShare. Use when the user asks for Chinese or global financial data, market quotes, securities fundamentals, fund data, index data, futures data, bond data, macro data, or explicitly mentions AKShare or akfamily.
description_cn: 通过 AKShare 查询免费的金融市场、股票、基金、指数、期货、债券、宏观和公司数据。
license: Apache-2.0
metadata:
  author: deepseek-cowork team
  version: "1.0"
security_level: medium
allowed-tools: [query_akshare_data]
---

# AKShare 金融数据

这个 skill 使用 AKShare 查询免费金融数据。AKShare 的数据接口很多，具体函数名和参数以当前运行时安装的 AKShare 版本为准，可参考 http://www.akfamily.xyz/#/data。

## Capabilities

1. 调用 AKShare 公开函数并返回结构化 JSON。
2. 支持通过 `kwargs` 传递 AKShare 接口参数。
3. 支持 `limit` 限制返回行数，避免大表占用过多上下文。
4. 支持按 `columns` 选择需要返回的列。

## Usage Guidelines

- 优先使用 `query_akshare_data`，传入 AKShare 函数名，例如 `stock_zh_a_spot_em`。
- 当用户没有给出具体函数名时，先根据需求选择最接近的 AKShare 接口；不确定时说明函数选择依据。
- 对时效性强的数据，回答时说明查询时间和数据源可能存在延迟。
- 返回表格数据后，先检查 `ok`、`error`、`shape`、`columns`、`truncated`，再组织答复。
- 数据仅供研究参考，不构成投资建议。

## Safety Boundaries

- 只调用 AKShare 模块中的公开函数。
- 不执行任意 Python 代码。
- 不把用户输入当作模块名、表达式或脚本执行。
- 不承诺数据完整性、实时性或投资收益。

## Interface Details

### `query_akshare_data`

Parameters:
- `function_name`: AKShare 函数名，如 `stock_zh_a_spot_em`。
- `kwargs`: 传给 AKShare 函数的关键字参数对象，默认 `{}`。
- `limit`: 返回行数上限，默认 `50`，最大 `500`。
- `columns`: 可选列名数组；为空返回全部列。
- `include_columns`: 是否返回完整列名列表，默认 `true`。

Returns JSON with:
- `ok`
- `function_name`
- `akshare_version`
- `shape`
- `columns`
- `records`
- `truncated`
- `warning`
- `error`
