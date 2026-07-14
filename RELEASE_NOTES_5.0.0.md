# DeepSeek Cowork 5.0.0 发布说明

> 版本：**5.0.0** · 文档基线：**2026-07-14** · 版本来源：`core/app_version.py`

5.0.0 是 DeepSeek Cowork 从“可运行的 Agent 原型”走向“可交付的桌面工作台”的版本。发布重点不是增加单一工具，而是把对话、项目边界、执行过程、技能、自动化和交付物预览整理成一条可恢复、可观察的工作流。

## 这次发布解决什么问题

- **任务有边界**：独立聊天自动获得 `conversation_workspaces/<session_id>/` 工作目录，项目聊天只在用户明确绑定的项目工作区内操作。
- **过程看得懂**：推理、工具调用、运行中引导、阶段回复和最终回答按时间线呈现；技术详情将代码、JSON、stdout、stderr 和 Traceback 分开查看。
- **结果接得上**：Markdown、HTML、图片、PDF、DOCX、PPTX、XLSX 可在右侧抽屉中预览；HTML 工作稿可以继续生成 PPTX、DOCX 或 PDF。
- **能力可复用**：Skill Center 支持发现、配置、调试、导入导出和标准 Agent Skill 安装；会话经验可以先生成草稿，再由用户确认后沉淀为 Skill。
- **自动化可管理**：Automation Center 以提示词任务为核心，支持计划、引用能力、Agent 绑定和运行历史。

## 主要亮点

### 桌面工作台

- 浅色 Linear 风格的紧凑导航、主内容区页面和右侧上下文抽屉。
- 设置、能力、自动化作为稳定的主内容区入口，返回后保留会话、筛选、滚动和抽屉状态。
- Toast、确认、输入和任务交互统一为应用内反馈，失败时保留输入并展示恢复路径。
- 启动阶段优先显示可交互主界面，Skill 索引、MCP 探测、历史分页和 WebEngine 预览按需或后台加载。

### 对话与执行

- 模型选择按对话保存，运行中的一轮使用提交时冻结的模型快照。
- 支持剪贴板图片附件；文本模型会在发送前明确阻止，不会静默丢弃图片。
- 支持运行中补充引导，并在安全节点应用；历史消息可原位编辑并从该点重新生成。
- 会话使用 SQLite 持久化，打开历史前等待保存队列落盘，读取失败显示明确错误而不是空会话。

### PPT 与办公交付

- 回复末尾提供自由、PPT、设计稿和 DOCX 四种办公稿输出 profile。
- 内置 PPT Agent 支持自动策略、网页演示、技术分享、商业汇报和模板化办公 PPT 偏好。
- PPT Agent 可选择 Guizang PPT Skill、Frontend Slides、Huashu Design 等 html-ppt 策略，并统一先产出 HTML 工作稿。
- HTML 转 PPTX 时支持选择模板，保留模板主题、母版、字号、色彩、版式节奏和顶部/底部图片。

### 技能、MCP 与自动化

- Skill 继续坚持“tool 负责执行、skill 负责经验”的单一执行面。
- 支持 `config_fields` 和 `mcp_server_presets`，凭据由本地配置注入，不写入随包文件。
- MCP 支持 `stdio` 和 Streamable HTTP；腾讯文档、飞书文档、钉钉文档、WeKnora、ShowDoc MCP、Airflow 和官方 Superset MCP 以可选能力提供。
- 并行工具调用仅允许已发现的只读工具，写入、审批、安装和破坏性操作不会进入并行路径。

## 兼容性与迁移

- 打包运行环境：Windows；源码运行要求 Python 3.10+。
- 模型接入：OpenAI-compatible 与 Anthropic；具体模型能力以服务商和当前配置为准。
- 旧 Skill 目录仍支持仅含 `impl.py` 的兼容模式；标准 Agent Skill 保留上游根目录 `SKILL.md`。
- 旧版会话和自动化配置会按当前迁移规则处理；旧 SOP 模板任务会停用保留，需要补充提示词后再启用。
- 旧版 `.doc`、`.ppt`、`.xls` 暂不提供内置预览，请先转换为 `.docx`、`.pptx` 或 `.xlsx`。

## 升级与发布前检查

发布包维护者建议按以下顺序验收：

1. 从 GitHub Release 下载并解压 ZIP，确认 `deepseek-cowork.exe` 可以启动。
2. 在 **设置 → 模型与服务** 配置一个可用模型，并验证普通对话、工具调用和失败提示。
3. 分别验证独立聊天、项目聊天、归档/恢复和历史会话恢复。
4. 安装文档或数据分析工具包后，验证一个真实文件任务和一个交付物预览任务。
5. 验证 PPT Agent → HTML → PPTX（含模板和不含模板）链路，以及取消/失败后的恢复状态。
6. 验证 Skill 配置、MCP 调试、自动化手动触发和运行历史。
7. 检查 `daemon.log`、`ppt_agent_debug.log`、`ui_navigation.log` 和 `native_crash.log` 是否能覆盖提交、启动、运行、完成和错误路径。

## 文档导航

- [README_CN.md](README_CN.md)：中文概览、安装和快速开始
- [README.md](README.md)：English overview and installation
- [USER_GUIDE.md](USER_GUIDE.md)：图文使用指南
- [PRODUCT_DOC.md](PRODUCT_DOC.md)：产品定位、功能和典型流程
- [TECHNICAL_DESIGN.md](TECHNICAL_DESIGN.md)：架构、运行模型和持久化设计
- [SKILL_SYSTEM.md](SKILL_SYSTEM.md)：Skill、Tool、MCP 和渐进披露模型
- [ROADMAP.md](ROADMAP.md)：5.0.0 发布基线之后的项目路线

