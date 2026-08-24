---
name: mysteel-data
description: 钢联数据大宗商品能力包；仅在用户明确要求使用钢联或 Mysteel 数据进行查询、分析、研报、商机、天气、海关或图表生成时使用。
---

# 钢联数据（Mysteel）

本能力包整合钢联数据发布的 9 个 Skill，通过能力中心统一配置并在 Cowork 沙盒中执行。

## 能力路由

- `customs_query`：海关进出口数据。
- `balance_query`、`balance_field_mapping`：农产品供需平衡表及字段单位。
- `weather_query`：农产品主产区天气。
- `bidding_search`、`supply_demand_search`：招投标与钢材现货供需商机。
- `info_search`：大宗商品实时资讯。
- `market_analysis`：钢联市场分析。
- `report_outline`：钢联研报梗概。
- `chart_generate`、`chart_render`：ECharts 配置生成与安全 HTML 渲染。
- `price_search`：价格、宏观和产业链指标问数，可按需生成 CSV。

## 配置与运行

1. 在能力中心配置 `MYSTEEL_API_KEY`。不要要求用户设置系统环境变量、`.env`、命令行密钥或 Skill 目录凭据文件。
2. 调用前读取匹配的子 Skill 文档，再使用 `run_skill_script` 执行已声明入口；不要通过 Bash、文件搜索或任意 Python 代码绕过入口。
3. 价格 CSV、图表配置和 HTML 默认写入当前会话工作区的 `mysteel/output`。只有用户明确指定时才传入工作区内的输出路径。
4. 查询文本、筛选条件及图表数据会发送到钢联数据 API。回答中注明钢联数据来源和查询时间；金融与产业信息仅供研究参考。

## 安全边界

- 本包所有远端操作均为查询或内容生成，不修改钢联账户远端状态。
- 不把 API Key 放入参数、日志、回复、产物或 Skill 文件。
- 不自动切换数据源，不隐藏 API/业务错误，不把失败结果解释为成功。
- 网络异常或服务端 5xx 最多自动重试一次；鉴权、参数和其他业务错误不重试。
- 输出路径必须位于当前会话工作区内；不得覆盖或自动删除既有产物。

## 子 Skill 参考

按任务只读取一个匹配文档；组合任务再读取必要的其他文档：

- [海关数据查询](customs-reporter/SKILL.md)
- [农产品供需分析](balance-sheet-reporter/SKILL.md)
- [气象数据查询](weather-reporter/SKILL.md)
- [商机探查](bid-supply/SKILL.md)
- [实时资讯](info-search/SKILL.md)
- [市场分析](market-analysis/SKILL.md)
- [研报撰写](report-write/SKILL.md)
- [智能图表生成](chart-generation/SKILL.md)
- [智能问数](price-search/SKILL.md)
