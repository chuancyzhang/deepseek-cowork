---
name: mysteel-bid-supply
description: 使用钢联数据搜索近三个自然年的招投标信息，或查询钢材现货供应与求购商机。
---

# 商机探查

> Cowork 适配：密钥由能力中心注入；不保存凭据，也不向用户隐藏数据来源。

## 招投标

调用 `bidding_search --query <文本>`；可选毫秒时间戳 `--start-time`、`--end-time` 和 `--top-k`。仅支持当前自然年及前两个自然年。

## 钢材现货供需

调用 `supply_demand_search --type 1|2`：`1` 表示供应信息（买家找货源），`2` 表示求购信息（卖家找买家）。可按品种、规格、材质、钢厂、地区和仓库筛选。详细意图规则见 [意图指南](references/intent-guide.md)。

