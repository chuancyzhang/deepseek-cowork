# Source

The nine archives below were supplied by the user from the official Mysteel
Skills distribution on 2026-08-24. None of the archives included a license
file. Redistribution authorization must be confirmed before a public release.

| Archive | Upstream root | SHA-256 |
| --- | --- | --- |
| `海关数据查询.zip` | `Mysteel_CustomsReporter` | `58489EFD174D9E7BE3B2F1C96266F3EA4CA7D7C7FADC20E2C99FD93A64CFFA6C` |
| `农产品供需分析.zip` | `Mysteel_ BalanceSheetReporter` | `94E287510618E9890B1ADA9EAACE51A90B656F25ABABD36A4B4A248E085030BB` |
| `气象数据查询.zip` | `Mysteel_WeatherReporter` | `EFEB4A4938228FE72C62259A000B73C8979C681BF415BA7FE73344ECC0CEBBEB` |
| `商机探查.zip` | `Mysteel_BidSupply` | `40CEA09182AB4212994BE548633410CFDFBD7287118115F149E3A615843DD7DD` |
| `实时资讯.zip` | `Mysteel_InfoSearch` | `B7600DB27907C3DD44B4297036F909623F2BA498FF44EDF8E460959430ADE0BA` |
| `市场分析.zip` | `Mysteel_MarketAnalysis` | `136E83C7946CDDF8A7C39BFD2B1661C64DF5FC7D4980E2C9CD8AD7BF755017C0` |
| `研报撰写.zip` | `Mysteel_ReportWrite` | `98D27A50817C2CB6247FB69BB7C8E495FA425D25F7D5B72290850B25CC26EBFA` |
| `智能图表生成.zip` | `Mysteel_ChartGeneration` | `6D6481C9942CA9EAB00A047DB83632E4EE18424F896503FF94AE06D4DB95ADB4` |
| `智能问数.zip` | `Mysteel_PriceSearch` | `3763F2A1D60B3A4781275F2EDF047013F738F5C170D6F5D807E404C3D16692E3` |

## Cowork adaptations

- Added one default-off parent capability and unified capability-center configuration.
- Ported the twelve Node.js entrypoints to Python for the bundled Cowork sandbox.
- Replaced file and command-line credentials with `MYSTEEL_API_KEY` runtime injection.
- Constrained generated artifacts to the active Cowork workspace and removed automatic deletion.
- Added bounded read-only retries, explicit business errors, UTF-8 output, and credential-safe diagnostics.
- Preserved the upstream endpoint selection and task-specific parameter semantics.

