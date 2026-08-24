---
name: mysteel-customs-reporter
description: 使用钢联数据查询指定商品或 HS 编码的海关进出口月度或汇总数据。
---

# 海关数据查询

> Cowork 适配：密钥由能力中心注入，产物不得写入 Skill 目录。上游文件密钥和 OpenClaw 固定路径说明不适用。

调用 `customs_query`。`--product-name` 与 `--hs-code` 二选一；必须提供 `--start-date YYYY-MM`、`--end-date YYYY-MM` 和 `--trade-type import|export`。可选 `--cc-type cny|usd`、`--country`、`--data-type monthly|summary`。

单次调用只查询一个商品/HS 编码、一个贸易方向和一个国家；比较任务应分次查询后再汇总。详细字段见 [API 参数](references/api.md)。

