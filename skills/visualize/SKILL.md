---
name: visualize
description: Build in-conversation interactive charts, explorers, simulations or interface previews when interaction materially helps understanding. Use Markdown for ordinary tables, Mermaid for static relationships and project tools for standalone sites.
description_cn: 在对话中生成有用的交互图表、探索器、模拟器和界面预览。
type: builtin
source_type: builtin
default_enabled: true
metadata:
  author: deepseek-cowork team
  version: "2.0"
allowed-tools: [run_visualization_python, finalize_inline_visualization]
---

# 交互可视化

选择最小且能解释问题的表达：普通对照用 Markdown 表格，静态节点关系用 Mermaid；参数变化、筛选、模拟和探索适合本技能。报告/PPT 静态图使用绘图库，网站、独立文件或项目修改按对应任务处理，不因有图表就生成内联内容。

## 内容与交互

- 初始画面就有用，只提供对当前任务必要的控件，不机械加入搜索、重置、评分卡或装饰面板。
- 数据分析先用 data-analysis 核验口径与结果，再传入绘图所需值。单位、时间范围、缺失和抽样如实呈现，不拿演示数据替代真实结果。
- 每个控件真实更新图形和数值；筛选后的结论也要成立。空结果、零和缺失分别处理。
- 图表、模拟或界面布局任务按需读取 [图表与交互指导](references/charts.md)，路径见 References。

## 生成与发布

1. 通过 run_visualization_python 计算必要数据，并从环境变量 COWORK_VISUALIZATION_DIR 取得输出目录。
2. 写入 ASCII 小写连字符命名的 .html 文件，只能是 HTML Fragment，不含 doctype、html、head、body。写真实换行和 HTML，安全序列化内联数据，不能留下字符串转义残片。
3. 检查数据计算、DOM 引用、变量和主交互代码，再调用 finalize_inline_visualization。
4. 将返回 directive 原样单独放在最终回复的显示位置，不包装成代码块或链接。
5. 修改后重新生成并发布，不覆盖已发布版本。发布只验证文件契约和资源限制，不等于完成交互或视觉检查。实际检查以当前可用预览为准，未运行不能声称运行通过。

## Fragment 契约

- UTF-8，最多 2 MiB，大数据先聚合、分箱、抽样或降低无用精度。
- 根节点有唯一 id，用 document.getElementById 取得根节点；CSS、查询和事件限定在此根节点。检查重复 ID、缺失元素及未定义变量。
- 数据内联，禁止 fetch、XHR、WebSocket、表单提交或运行时本地文件访问。
- 优先原生 HTML、SVG、Canvas、CSS 和 JS。SVG/XML 命名空间 URI 不算网络资源。
- 确有需要才用固定版本 HTTPS 静态资源，允许 CDN：cdnjs.cloudflare.com、esm.sh、cdn.jsdelivr.net、unpkg.com、fonts.googleapis.com、fonts.gstatic.com、fonts.bunny.net。其他来源被拒绝；资源或脚本失败需修正，不把空白图当成功。
- 使用宿主实际提供的 --background、--foreground、--muted-foreground、--border、--muted、--accent、--accent-foreground、--primary、--primary-foreground、--destructive、--viz-series-1 至 6、--font-size-base，不假设上游组件样式、图标或 window.openai API 存在。
- SVG/CSS 用主题变量，Canvas 或图表库先解析颜色；监听 window 的 cowork:themechange 事件重新绘制。类别颜色映射稳定，不假定某个系列永远是某种颜色。
- 填满可用宽度，支持 320px；不能固定外宽、position:fixed 或 viewport-height 布局。窄窗口重排控件、减少刻度，不整体缩小文字。
- 使用原生 button/input/select/textarea，提供可访问名称、键盘和焦点；图形有 title/desc 或可访问摘要，关键信息不只依赖颜色或悬停。

## 展示状态

需要恢复筛选时先 await window.cowork.loadState()，校验并应用保存值，再首次绘制；有意义的交互后 window.cowork.saveState({selectedId, filters})。该方法不返回保存成功承诺，失败由宿主显示。

只保存展示状态，JSON 对象最多 64 KiB；不保存凭据、原始文件、图片或可重新计算的大量数据。状态不发起模型消息、不授权外部操作。默认值适用于缺失或不兼容的状态，不能跨会话共享。

最终回复仅保留必要结论与发布指令，例如：

```text
::cowork-inline-vis{file="customer-explorer-a1b2c3d4.html"}
```
