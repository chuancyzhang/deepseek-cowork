---
name: mysteel-balance-sheet-reporter
description: 使用钢联数据查询农产品年度供需平衡表，并解释字段和单位。
---

# 农产品供需分析

> Cowork 适配：密钥由能力中心注入；不读取或更新本地凭据文件。

1. 先从 [支持品种](references/varieties.md) 确定 `breed-class` 与 `breed-name`。
2. 调用 `balance_query --breed-class <大类> --breed-name <品种> --area <国家地区> --crop-year <YYYY年度>`。
3. 调用 `balance_field_mapping --breed-name <品种>` 获取字段含义和单位，再解读产量、消费、进口、出口和期末库存。

一个请求只查询一个品种、地区和年度；多维比较使用多次独立查询。

