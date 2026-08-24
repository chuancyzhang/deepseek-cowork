---
name: mysteel-chart-generation
description: 使用钢联 AI 图表服务将文本或结构化数据生成 ECharts 配置，并渲染为工作区 HTML。
---

# 智能图表生成

> Cowork 适配：密钥由能力中心注入；任务文本和提供的数据会发送到钢联。配置及 HTML 只能写入当前会话工作区。

1. 调用 `chart_generate --task <描述>`。自然语言默认 `FREEDOM`；明确标准图表可用 `TEMPLATE --type <类型>`；结构化数据可用 `STRICT --data <JSON> --data-example <JSON> --data-description <说明>`；不确定时使用 `AUTO`。
2. 从返回值读取实际 `option_file`，再调用 `chart_render --option-file <路径>`。
3. 向用户返回实际 HTML 路径，并说明页面加载 ECharts CDN。

详细模式与参数见 [图表参数](references/api-parameters.md)。不得让服务在没有真实数据时“推断或生成”看似真实的市场数据。

