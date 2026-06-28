# DeepSeek Cowork 技术设计

当前应用版本：**4.9.5**

## 1. 设计目标

DeepSeek Cowork 的目标不是做一个“会聊天的 IDE”，而是做一个桌面 Agent 工作台：

- 用单轮内交错式推理和工具调用完成真实任务
- 把本地文件操作约束在项目工作区边界内
- 用技能系统提供经验，而不是引入第二套执行协议
- 用桌面 UI 承载观察、确认、交付物预览和自动化管理

## 2. 总体架构

系统由五层组成：

1. **UI 层**
   `main.py` 提供对话界面、项目侧栏、右侧上下文抽屉、设置中心、自动化中心和技能中心。
2. **Agent 层**
   `core/agent.py` 负责推理循环、工具调度、模式约束和结果汇总。
3. **能力层**
   `core/skill_manager.py` 负责加载 `skills/`、`ai_skills/`、MCP 工具，并按需向模型披露技能内容。
4. **运行层**
   `core/daemon.py`、`core/sandbox_runtime.py` 与相关 provider 代码负责模型请求、后台执行和运行时环境。
5. **数据层**
   `core/chat_storage.py`、`core/config_manager.py`、`core/memory_store.py`、`core/automation_manager.py` 负责会话、配置、记忆和自动化状态持久化。

## 3. 关键界面结构

- **项目式对话**：纯对话不绑定工作区；项目对话把文件能力限制在对应目录内。
- **生成办公稿**：AI 回复末尾的消息级动作，按自由、PPT、设计稿、DOCX 注入办公交付策略，并把该生成轮次默认折叠为任务卡；结果文件入口保持外显。
- **右侧上下文抽屉**：文件、交付物、任务观测、子 Agent 状态默认隐藏，按需展开；文件区域在浏览态和详情态之间切换，聊天交付物入口直接进入详情态。
- **设置中心**：集中配置模型、智能体、工作区、MCP、企业消息、更新和运行组件。
- **自动化中心**：承载模板、计划任务、执行历史和人工确认。

这套结构的核心目的是把“执行过程”和“产品操作”留在同一个窗口内完成，而不是依赖外部终端或浏览器页面。

## 4. Agent 运行模型

Cowork 采用交错式推理流程：

1. 读取当前会话、工作区和运行模式。
2. 按需注入稳定系统策略、记忆摘要、本次消息动作 workflow 和最小技能信息。
3. 由模型直接调用工具。
4. 把工具结果回传给模型继续推理。
5. 产出最终回答，并把过程事件同步到 UI。

几个关键约束：

- 直接执行面只有 `tool`
- `skill` 只负责经验和边界，不作为独立调用协议
- 生成办公稿使用本次请求级 `workflow_mode = office_html_first` 和 `office_output_profile` 注入提示；从 HTML 继续生成 PPTX、DOCX、PDF 使用 `workflow_mode = office_file_conversion` 和 `office_conversion_target` 标记；两者都不新增 `RUN_MODE`，因此不改变工具权限。
- UI 通过用户消息 `meta.workflow_mode` 将该轮用户消息、工具调用和助手结果渲染成可展开的办公任务卡；消息本体仍按原结构持久化，交付物文件卡渲染在折叠过程外。任务卡会合并最终回复、隐藏气泡识别到的文件卡路径和本轮工具变更中的有效工作区文件，避免最终回复未写完整路径时结果区为空。历史消息按 render span 独立渲染，避免普通消息和办公任务混排时丢失折叠状态。
- 从 HTML 生成 PPTX 时可附加 PPTX 模板文件，提示要求以 HTML 为内容源、模板为视觉结构源，并保留模板主题、母版、字号、色彩、版式节奏和顶部/底部图片；PPTX、DOCX、PDF 转换提交后只更新交付物详情页底部的局部运行态，不阻塞抽屉关闭或切换，完成后仍通过任务卡、Toast 和交付物扫描回填结果。
- `tool_search` 负责延迟发现工具与匹配技能
- `parallel_tools` 只允许并行只读调用

## 5. 技能与工具

能力来源有四类：

- `skills/`：核心内置技能
- `ai_skills/`：随包可选插件与用户技能
- MCP servers：通过 `stdio` 或 Streamable HTTP 接入的外部工具
- 常驻执行工具：例如 `run_python_code`

设计原则：

- 工具负责执行
- 技能负责经验
- 技能按需披露，默认只给最小摘要
- 用户可以通过“沉淀为 Skill”把会话经验转为可复用能力

详细模型见 [SKILL_SYSTEM.md](SKILL_SYSTEM.md)。

## 6. 自动化设计

自动化分成两层：

- **会话模板层**：`core/sop_manager.py` 管理步骤、执行器、人工确认和推进状态
- **调度层**：`core/automation_manager.py` 管理计划任务、下次执行时间和运行历史

特点：

- 模板与计划任务共用同一套步骤模型
- 每次只执行当前步骤，不把整条 SOP 一次性塞进模型
- 人工确认保留在聊天流中，避免脱离上下文操作

## 7. 数据与持久化

- **会话**：`core/chat_storage.py` 负责本地消息历史、归档、置顶和项目归属
- **配置**：`core/config_manager.py` 统一管理模型、MCP、工作区、智能体和 UI 偏好
- **记忆**：`core/memory_store.py` 与 `core/memory_update.py` 管理灵魂提示词、全局摘要和工作区摘要
- **技能**：文件系统中的 `SKILL.md`、`skill.json`、`impl.py`、`experience/entries.jsonl`
- **自动化**：模板、计划任务和执行历史保存在本地配置数据中

## 8. 运行环境

- 桌面 UI 基于 PySide6
- 模型接入支持 OpenAI-compatible 与 Anthropic
- 打包模式保留 Python 基础运行时和 Git Bash
- Node.js、文档读取、浏览器自动化等依赖改为设置中的可选组件
- 打包版内置 MCP client 运行时，便于直接接入 MCP tools

## 9. 设计取舍

- **优先产品化桌面体验**：把工具链、观察和交付物预览做进 UI，而不是只暴露底层能力。
- **优先单一执行面**：不让 skill、automation、memory 各自演化成新的执行协议。
- **优先显式边界**：工作区、模式、人工确认和技能披露都尽量显式可见。
- **优先可演进性**：用户技能、MCP 工具、自动化模板和长期记忆都可以持续扩展。

## 10. 代码入口

- `main.py`
- `core/agent.py`
- `core/daemon.py`
- `core/skill_manager.py`
- `core/mcp_client.py`
- `core/sop_manager.py`
- `core/automation_manager.py`
- `core/chat_storage.py`
- `core/config_manager.py`

如果需要产品视角的说明，见 [PRODUCT_DOC.md](PRODUCT_DOC.md)；如果需要快速上手，见 [README_CN.md](README_CN.md)。
