---
name: mysteel-price-search
description: 使用钢联数据查询大宗商品期现货价格、宏观指标、产业链供需、进出口、库存及金融市场指标。
---

# 智能问数

> Cowork 适配：密钥由能力中心注入；不读取本地凭据文件，不自动删除历史 CSV。

调用 `price_search <查询文本>`。需要保存指标明细时加 `--csv`，并使用 `--limit`、`--days`、`--start-date YYYY-MM-DD` 或 `--end-date YYYY-MM-DD` 控制范围。

CSV 默认保存到当前工作区 `mysteel/output/price-search`，每次生成唯一文件。回答必须标明引用的 `indexName`、单位、日期范围、钢联数据来源和查询时间。

