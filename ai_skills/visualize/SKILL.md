---
name: visualize
description: Generate interactive charts, data explorers, diagrams, simulations, or UI previews as executable HTML fragments embedded directly in the conversation.
description_cn: 生成可直接嵌入对话的交互图表、数据探索器、Diagram、模拟器或 UI Preview。
type: bundled_plugin
source_type: bundled_plugin
default_enabled: false
metadata:
  author: deepseek-cowork team
  version: "1.0.0"
  permissions: ["workspace_read", "code_execution"]
security_level: medium
allowed-tools: [run_visualization_python, finalize_inline_visualization]
---

# Visualize

仅在交互视图能明显改善理解、比较或探索时使用本插件。静态结构能用普通 Markdown 或 Mermaid 说明时，不要生成内联可视化。

## Workflow

1. 使用 `run_visualization_python` 读取当前工作区数据、计算、聚合和抽样。
2. Python 代码必须从环境变量 `COWORK_VISUALIZATION_DIR` 获取输出目录，并在其中写入一个 ASCII 小写连字符命名的 `.html` 文件。
3. 文件只能是 HTML Fragment；不要包含 `doctype`、`html`、`head` 或 `body`。
4. 调用 `finalize_inline_visualization` 校验并发布该文件。
5. 把工具返回的 `directive` 原样单独放在最终回复中需要显示交互视图的位置。不要把它放进代码块或 Markdown 链接。
6. 修改已有视图时重新生成文件并再次发布，不覆盖已发布版本。

## Fragment contract

- 总大小必须小于 2 MB。大数据应先聚合、分箱、抽样、降低精度或删除未使用字段。
- 根元素必须有唯一 `id`，脚本必须通过该 ID 获取根节点。
- 使用字面 HTML 和真实换行；不要把 `\\"` 或 `\\n` 留在最终文件中。
- 交互数据应内联。禁止 `fetch`、XHR、WebSocket、表单提交和运行时本地文件访问。
- 首版运行时完全离线；禁止 CDN、远程字体、远程图片和其他外部 URL。使用原生 HTML、SVG、Canvas、CSS 和内联 JavaScript。
- 使用 `--background`、`--foreground`、`--muted-foreground`、`--border`、`--accent`、`--primary` 和 `--viz-series-*` 主题变量；不要硬编码浅色或深色背景。
- 填满可用对话宽度，默认按 736px 设计并支持 320px；避免固定外宽、横向滚动、`position: fixed` 和 viewport-height 布局。
- 使用原生 `button`、`input`、`select`、`textarea`，提供标签、键盘操作和 SVG `<title>/<desc>`。
- 每个选择、筛选或参数变化都必须真实更新视图。生成后检查所有查询元素存在且 JavaScript 无未定义变量。

## State

需要在历史会话中恢复筛选或选择时：

```javascript
const saved = await window.cowork.loadState();
// apply saved values before the first render
window.cowork.saveState({ selectedId, filters });
```

只保存展示状态。不要保存密码、文件内容、凭据或超过 64 KB 的数据。

## Output

最终回复仅保留必要的简短说明和发布工具返回的指令，例如：

```text
::cowork-inline-vis{file="customer-explorer-a1b2c3d4.html"}
```
