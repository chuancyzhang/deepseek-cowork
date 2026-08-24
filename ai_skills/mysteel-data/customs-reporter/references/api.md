# 海关数据 API 参数

端点：`POST /mcp/custom/queryData`。

| CLI | 请求字段 | 说明 |
| --- | --- | --- |
| `--product-name` | `productName` | 商品名称，与 HS 编码二选一 |
| `--hs-code` | `hsCode` | 海关税则号，支持前 6 位 |
| `--start-date` | `startDate` | 开始年月，`YYYY-MM` |
| `--end-date` | `endDate` | 结束年月，`YYYY-MM` |
| `--trade-type` | `tradeType` | `import` 或 `export` |
| `--cc-type` | `ccType` | `usd`（默认）或 `cny` |
| `--country` | `country` | 中文国家或地区名 |
| `--data-type` | `dataType` | `monthly`（默认）或 `summary` |

