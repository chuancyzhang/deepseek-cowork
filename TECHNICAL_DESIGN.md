# DeepSeek Cowork 架构设计

交付物预览按格式分发：HTML/Markdown 使用 WebEngine，图片使用 QPixmap 内嵌预览，PDF 优先使用 QtPdf 且在组件不可用时提供 pypdf 文本预览，DOCX/PPTX/XLSX 使用应用内结构化 HTML 预览，不依赖用户本机 Microsoft Office。

项目团队：**deepseek-cowork team**。

当前应用版本：**4.9.3**。

*   **同轮中途引导**：桌面主对话的 `LLMWorker` 维护线程安全的 FIFO guidance 队列，在模型请求前、工具结果返回后及最终收敛边界消费；当前流式响应和正在执行的工具不会被强制打断。daemon 通过 `turn_id`、`turn_started` 与 `steer_message` 协议校验活动轮次，避免迟到输入串入下一轮。引导沿用普通用户消息的 `content` / `content_parts` / 附件元数据，停止或异常时仍保留已接受内容。

## 1. 架构理念

DeepSeek Cowork 采用 **Interleaved Chain-of-Thought** 架构，在推理阶段直接调用工具，实现“思考-执行-再思考”的闭环，降低幻觉并提升任务成功率。

## 2. 核心组件

### 2.1 UI 层 (PySide6)
*   **main.py**：桌面入口，负责窗口、聊天气泡、工具调用卡片、右侧上下文抽屉等 UI 交互，并根据窗口与抽屉状态动态计算主会话区宽度。
*   **项目式左侧栏**：顶部入口创建无项目纯对话，项目行 `+` 创建项目会话；项目名称区域使用可压缩尺寸策略，右侧固定宽度操作区承载 `+` 与更多按钮，避免系统字体、侧栏宽度或 DPI 变化把按钮挤出。相关符号由 Qt 绘制，不依赖图标字体；项目图标操作仅设置可访问名称，不启用原生 Tooltip，规避部分 Windows 主机的黑色提示框。项目标题只展开/收起会话预览，当前项目与文件页始终跟随当前可见会话。
*   **会话级工作区边界**：`SessionState.workspace_dir` 是运行与持久化的工作区来源；激活无项目会话会清除旧项目上下文。输入栏下方的项目选择器是唯一连接入口，可搜索配置中的未隐藏项目、添加项目或回到纯对话，但仅允许尚无消息且未运行任务的空会话切换。`run_context.workspace_mode` 区分 `chat_only` 与 `project`，工具注册根据 handler 是否接收 `workspace_dir` 标记依赖，并在纯对话的工具列表和发现结果中统一过滤。
*   **右侧上下文抽屉**：文件、任务观测、子 Agent 监控以隐藏抽屉承载；文件页内用“全部文件 / 交付物”分段承载工作区文件树和最近交付物列表，并共用同一套预览栈。助手回复由工作区约束的路径识别器生成 `cowork-file` 内部链接和消息末尾文件卡片，点击后再次验证路径并直达交付物视图；流式内容形成完整有效路径时同步更新卡片。当前会话已打开交付物视图后，后续新路径自动选择并渲染，关闭抽屉或切换到全部文件即停止跟随，同一路径文件更新仍标记过期并等待手动刷新。HTML 与 Markdown 通过延迟加载的 QtWebEngine 渲染；图片由 QPixmap 解码并按预览区域等比缩放；PDF 使用延迟加载的 QtPdf，并在创建 `QPdfView` 时绑定同一个 `QPdfDocument`，组件不可用时使用 pypdf 显示文本预览；DOCX/PPTX/XLSX 由 python-docx、python-pptx、openpyxl 生成结构化 HTML 预览，旧版 DOC/PPT/XLS 二进制格式显示明确不支持提示。
*   **动态对话阅读列**：消息列表与输入栏保持原有视觉样式，并根据主窗口可用宽度、右侧抽屉开合状态和保底留白动态计算；项目栏默认宽度为 232px，主区水平边距为 12px，抽屉外边距与中右栏间距为 8px。抽屉关闭时对话列使用居中的舒适阅读宽度并允许右侧留白；抽屉打开时继续按可用空间避让，窄窗口仍由 compact minimum 与 drawer width limit 防止重叠。
*   **会话工具栏**：添加文件、智能体提及、自动化模板绑定、指定能力、反问模式统一从输入区入口触发。
*   **设置中心**：设置弹窗采用更接近 Apple 桌面偏好设置的左侧导航 + 右侧内容区结构，内容区使用轻量无边框分区；模型渠道可在后台线程中用未保存的地址、密钥、当前模型和 20 秒请求超时执行真实连接测试。常规文案偏产品表达，MCP 相关术语保持英文。
*   **模型与推理选择**：对话栏模型菜单使用 `channel / model` 标签区分同名模型。OpenAI-compatible 模型通过 profile 的 `reasoning_efforts` 声明允许档位，`reasoning_effort` 保存该模型上次选择；运行上下文把本轮档位显式传给 `LLMFactory`。没有能力声明时 UI 不显示推理项且 provider 不发送 `reasoning_effort`。
*   **消息原地编辑**：已完成的用户气泡可切换到内嵌编辑态；提交时在当前会话截断目标消息及其后续内容并重新生成。首条消息被截断时会短暂重建空状态，新气泡加入后无条件显式隐藏欢迎开屏，避免页面显隐时序令其重新出现；删除仅移除目标用户消息。运行中或历史尚未加载时明确阻止改写。
*   **对话置顶与归档**：项目内及无项目对话共用悬停操作区；`meta.pinned` 控制组内优先排序，`meta.archived` 控制侧边栏过滤，元数据局部更新不会改写 `updated_at`。项目区顶部、项目行和对话行的六类图标入口由 `SidebarHoverTipController` 在主窗口内部绘制浅色功能提示，绕开 Windows 原生 tooltip 黑块；其他控件仍使用 Tooltip 专用主题初始化。
*   **系统提示条**：`add_system_toast(...)` 在聊天流中渲染紧凑状态条，居中插入、限制最大宽度、允许换行，并跟随当前消息列宽度重算；颜色仅作为轻量状态提示而不是整块警示背景。会话自动化等待人工确认时也在聊天流中插入操作条，承载确认、重跑和标记不适用。
*   **多模态附件建模**：输入区把普通文件记录为 `input_file`，把 PNG/JPEG/WEBP/GIF 记录为 `input_image`；provider 在发送前再决定是否转换成视觉请求。
*   **自动化中心**：侧边栏独立入口，承载已配置任务、执行历史与任务模板管理；定时计划支持快捷配置和 crontab 表达式双入口。
*   **可视化监控**：展示子任务状态、思考过程、工具参数与工具结果。子 Agent 面板按时间线拆分显示任务输入、工具调用、工具结果、流式输出与最终输出；任务观测会区分稳定 prompt、runtime context 与已披露 skill context，并显示 provider 返回的 cached token usage 与命中率。
*   **后台 daemon 连接**：UI 只发起短任务排队，不在点击开始或自动化分发时同步等待 daemon ping/retry；daemon 请求会在后台合并，避免重复点击堆出多个启动任务；daemon 未就绪时当前请求立即走本地 worker。
*   **分阶段后台启动**：主窗口先完成显示，再延后补默认工作区、侧边栏历史、托盘、daemon 预热、daemon monitor 和自动化调度，减少首屏阶段的同步负载。
*   **单窗口与后台进程锁**：UI 主进程在入口处持有运行锁，重复点击 exe 会重试激活首个窗口而不是继续建第二个 UI；daemon 与企业消息网关子进程仍在入口处通过文件锁保证唯一实例，即使多入口同时触发也只保留一个存活进程。
*   **长对话轻量渲染**：长会话打开时不再先构造整段 render items；历史按跨度分页渲染，长回复流式阶段可临时走纯文本快路径，最终回复优先保留 Markdown / HTML 富文本渲染，仅极端巨大的普通文本才降级。
*   **流式合并与渲染缓存**：正文和思考流按短定时器批量刷新；稳定 Markdown / HTML 结果进入 LRU 缓存，历史回放和最终响应不再重复转换。
*   **聊天气泡虚拟化**：聊天区滚动时按视口 overscan 保留附近气泡，远离视口的历史气泡折叠成固定高度占位，恢复时复用原控件和缓存后的内容。
*   **异步会话持久化**：`save_chat_history()` 只在 UI 线程里整理快照并入队，后台 `ChatSaveWorker` 按会话合并、500ms debounce 后写入 SQLite；消息改写、长期记忆更新、会话重命名/归档/删除和应用退出前显式 flush，避免异步保存带来的读取时序问题。
*   **UI 到 Daemon 的上下文快照**：桌面会话交给 daemon 执行时会随请求传递当前 `state.messages` 快照；daemon 优先用该快照刷新内存会话，再追加本轮用户消息，避免 idle suspend 后从旧 SQLite 或旧缓存恢复导致上下文漂移。
*   **历史加载一致性屏障**：异步恢复会话期间，输入和保存请求均被拦截；加载完成后通过 `set_current_session()` 重新绑定窗口消息别名。历史迁移版本 3 会移除旧 query 模糊匹配生成的隐藏 Skill 上下文，同时保留 `tool_search` 和真实工具调用产生的上下文。
*   **主窗口偏好初始化**：`MainWindow` 在读取交付物布局、抽屉宽度和侧边栏排序等持久化偏好前先创建唯一的 `ConfigManager`，避免启动阶段访问尚未初始化的配置对象。
*   **运行时诊断日志开关**：高频子 Agent/UI runtime 日志默认关闭，仅当 `COWORK_RUNTIME_DEBUG_LOG=1` 时写入 `sub_agent_runtime.log`，避免状态流和磁盘 IO 绑定。
*   **UI 异常持久化**：`SafeApplication.notify(...)` 保留全局事件保护，但捕获异常时会将接收控件类型、事件类型和完整 traceback 始终追加到应用数据目录（便携模式为 `user_data/`）下的 `ui_error.log`；系统提示仅指向日志，不再把通用“继续运行”文案当作错误详情。
*   **反馈回路按钮**：侧边栏 `记忆` 打开可编辑的分层记忆中心并可触发生成草稿；`沉淀为 Skill` 保持会话知识沉淀入口。

### 2.2 Agent Core
*   **core/agent.py**：推理循环与工具调度，负责将用户输入转化为可执行任务。
*   **core/interaction.py**：桥接 UI 与推理流程，统一消息与工具调用格式。
*   **core/mcp_client.py**：封装 MCP `stdio` 与 Streamable HTTP 会话，负责连接测试、工具枚举与工具调用；对 `mcp` Python client 的新旧 Streamable HTTP API 做版本兼容。
*   **core/llm/providers.py**：在 OpenAI-compatible / Anthropic provider 边界把 `input_image` 转换成 base64 data URL 视觉块；未开启 `supports_vision` 时仅保留文本提示，因此 OCR 走模型能力而不额外引入本地 OCR 引擎。Anthropic 适配器将连续工具结果合并为紧随 assistant `tool_use` 的单条 user `tool_result` 消息，满足单轮多工具调用的协议约束。OpenAI-compatible provider 只在模型显式声明推理档位时发送通用 `reasoning_effort`，DeepSeek 请求继续附带 Thinking 参数；provider 还提供设置页使用的最小真实连接测试。OpenAI-compatible provider 会请求并解析 streaming usage 中的 cached token 细节；会话级 prompt cache key 仅在模型配置显式声明参数名时透传。
*   **core/sandbox_runtime.py / 打包 runtime**：随包保留 Python 基础环境与 Git Bash，Node.js 改为 AppData 可选运行时。文档等第三方库不再预装；五类工具包由 `core/runtime_components.py` 事务安装到临时目录，在隔离 `PYTHONPATH` 中验证全部声明模块后原子替换正式目录，只有带当前 schema、定义哈希和 Python 运行时标记的健康工具包才会注入沙箱。文档工具包显式包含 Pillow 与 ReportLab；若 `python-runner` 中的残缺同名模块遮蔽健康工具包，只清理发生冲突的顶层模块。Skill 私有依赖和自动修复保持兼容。QtWebEngine 及其传递模块继续随包。
*   **组件下载源**：Python 组件统一使用配置的 pip index，Node.js 使用配置的归档源并验证固定 SHA-256；自定义源仅接受无内嵌凭据的 HTTPS 地址，下载失败不静默换源。
*   **组件后台队列**：主窗口持有应用级组件任务管理器，安装、更新、修复和卸载严格单任务串行执行；设置窗口只订阅任务快照，因此关闭后任务仍继续，重开可恢复队列、进度和会话日志，完成或失败由托盘通知反馈。
*   **core/env_utils.py**：`ensure_package_installed(...)` 不再只依赖主进程 `importlib` 判断是否已安装，而是用沙盒 Python 直接验证目标模块可导入。对于 `python-runner`，若依赖状态缓存显示已安装但沙盒实际无法导入，会强制重装一次以修复失真的缓存记录；若最终失败，则把沙盒 traceback 回传，便于定位 `ImportError` / DLL load failure。
*   **core/process_utils.py**：集中提供 Windows 无控制台窗口的 subprocess 参数、进程单例锁和 runtime debug 日志开关，供 UI、updater、沙盒和系统技能复用，避免新增执行入口再次闪出 CMD。
*   **deepseek-cowork.spec**：内置 `python_env` 除 `Lib/` 和最小 `site-packages` 外，还要包含 Windows `DLLs/` 或同类平台扩展目录，以及常见 MSVC runtime DLL；否则 `_socket`、`_ssl` 一类标准扩展缺失，或 native wheel 在 `_internal/python_env` 中无法加载。

### 2.3 Daemon 与并发
*   **core/daemon.py**：无头推理服务，分离 UI 与模型推理负载。
*   **QThread**：UI 与后台线程解耦，保证界面响应。

### 2.4 技能系统
*   **core/skill_manager.py**：加载 `skills/` 与 `ai_skills/`，注入工具定义与经验。
*   **独立 Skill 托管**：`ai_skills/quant-strategy-management` 作为完整能力包接入，内部自带 Strategy DSL、存储、数据拉取、回测、报告和 CLI 入口，不依赖外部工程目录。
*   **浏览器自动化插件**：`ai_skills/browser-automation` 通过 Playwright over CDP 提供统一的串行浏览器动作协议。专用持久与临时隔离模式使用非默认 Chrome profile；Chrome 144+ 当前会话模式仅从本机 profile 的 `DevToolsActivePort` 连接回环端点，并要求用户先在 Chrome 中启用并批准远程调试。
*   **经验回写**：执行结果可回写到 `SKILL.md`，形成自进化闭环。
*   **能力包迁移与调试**：支持从单个 Skill 目录、Skill 集合目录或 ZIP 导入 Skill，支持将已有 Skill 单独导出或多选导出为集合 ZIP，并跳过缓存/构建目录；能力工作台和批量删除限制在 `ai_skills`，可验证 Skill 文件并调试 tools/scripts。
*   **显式只读并行**：`parallel_tools` 通过 `SkillManager.call_tool(..., require_read_only=True)` 执行子调用，保留顺序并遵守发现、模式和能力范围限制。
*   **core/skill_from_conversation.py**：把当前会话转录为可复用 Skill 草稿，并负责新建或更新 Skill 文件。

### 2.5 自动化、配置与存储
*   **core/sop_manager.py**：规范化自动化模板和会话运行态，维护 step/run 状态、步骤执行器元数据，并生成当前步骤 Prompt 片段。
*   **core/automation_manager.py**：规范化定时任务、计算 cron / 快捷计划的 next run、生成完整执行提示词并维护运行历史记录结构。
*   **core/config_manager.py**：统一配置入口，管理 API Key、Provider、`mcp_servers`、项目列表、工作区、自动化任务与运行历史。
*   **core/chat_storage.py**：历史对话持久化，按 `meta.workspace_dir` 支持项目分组，并以 `meta.pinned` / `meta.archived` 管理单条对话；局部元数据更新保留最近活动时间。SQLite 连接启用 WAL / busy timeout，并在普通追加路径下只写入新增消息，编辑、删除和迁移仍回退全量重写。旧版 `conversation_branch` 元数据可继续读取但不驱动 UI，旧版 `chat_history_*.json` 通过手动迁移写回 SQLite。
*   **core/memory_update.py / core/memory_store.py**：前者按全局或当前工作区扫描历史并生成草稿，后者管理灵魂提示词、全局/工作区摘要与版本备份；确认保存后才推进对应作用域的处理状态。
*   **core/updater.py**：检查 GitHub Releases，选择正式 ZIP 资产，下载前清理旧 ZIP、暂存目录、备份目录和更新脚本，校验解压结构并生成 Windows 更新脚本；PowerShell GUI 更新脚本允许前台窗口最小化，也支持默认最小化的后台安装，应用重启路径继续显式隐藏控制台窗口。

### 2.6 企业 IM
*   **core/im_gateway/**：多平台企业消息网关，接收飞书、钉钉与企业微信智能机器人事件并回传执行结果。
*   **会话映射**：IM 会话与本地会话保持一致的工作区边界，并按 provider 区分会话来源。
*   **上下文预算与溢出恢复**：Daemon 在 IM 绑定会话中按模型窗口估算上下文；DeepSeek V4 使用 1M token 预算，接近阈值才构造压缩历史，小窗口模型保守压缩，并对上下文长度错误自动重试一次。

## 3. 万物皆工具 (Everything Is a Tool)
- 工具即 `impl.py` 中的函数，解析签名动态生成 JSON Schema，作为 LLM 可调用的函数接口。
- `SKILL.md`：前言 (frontmatter) 提供元数据与 allowed-tools，正文提供使用指引；`experience` 字段承载自进化经验并在调用前注入。
- 动态导入与依赖自修复：缺失依赖时尝试自动安装并重试加载，提升技能首用成功率。
- 工具到技能映射：用于 UI 上报与提示注入。
- MCP 工具桥接：启用的 MCP server 会被注册成合成 skill，并把远端 tools 映射为带服务器前缀的延迟发现工具，继续复用 `tool_search`、工具可见性和调用链路。
- 常驻执行工具：`run_python_code` 在 execution 模式下默认暴露，并进入基础 system prompt 的“当前可用工具清单”，无需先通过 `tool_search` 发现。
- 视觉输入与工具发现：图片/截图输入不会再隐藏 `tool_search`；视觉回合与普通文本回合共用同一套延迟工具发现与 system prompt 能力描述，避免提示词仍要求搜索但 tool schema 实际缺失。
- 只读并行工具：`parallel_tools` 本身作为 always-allowed 元工具可默认暴露，但每个子调用必须是已发现、当前模式允许、能力范围允许且 `read_only=True` 的工具。
- 延迟发现刷新：`tool_search` 命中延迟工具后，下一轮会以追加方式更新 provider 的 tool schema，并在尾部 runtime context 中刷新“当前可用工具清单”；同一查询也会返回大小写不敏感匹配到的 AI skill 结果，供模型获取经验包上下文。
- 系统提示词能力分层：稳定 system prompt 只放长期策略和工具使用原则；当前真实暴露工具、运行模式、运行时路径和 SOP 状态放在请求尾部 runtime context。Office/PDF 读取需通过可选 `document-reader` 的 `document_read`，写入则由 AI 使用代码和任务所需库生成。
- Imported skill 全文暴露：默认只暴露元数据或用户明确选择的 brief；仅在明确选择、`tool_search` 命中或实际调用对应技能工具后，agent loop 才把完整 skill prompt 记录为隐藏会话上下文，并按 `skill_name + content_hash` 去重。普通用户文本不再触发本地 query 模糊匹配。
- Imported skill 执行约束：脚本型 imported skill 会在 skill metadata 和 `tool_search.skills` 中暴露 `preferred_tool = run_skill_script`、候选脚本名和执行提示，模型不再需要通过 `glob` / `bash` 反查 skill 目录。
- 异常 tool call 恢复：若 provider 流式返回缺少 `function.name` 的畸形 tool call，执行层会忽略该调用并追加恢复提示，而不是把这类半截调用当成正常工具步骤写入历史或 UI。

## 4. 数据流 (Data Flow)

1.  用户在 UI 或 IM 中输入指令。
2.  UI 将指令转交给 Daemon 的推理线程。
3.  Agent 进入 Interleaved CoT 流程：
    *   读取环境与文件（只读工具）。
    *   生成执行计划并调用写工具。
4.  工具结果回传给 Agent，完成最终回复。
5.  UI 渲染聊天气泡、工具调用卡片与状态变化。

### 4.1 手动反馈回路数据流

**长期记忆更新**
1.  用户从侧边栏打开 `记忆` 中心，可维护灵魂提示词和全局/工作区摘要，也可按当前摘要作用域发起生成。
2.  `MemoryUpdateWorker` 从 `core/chat_storage.py` 读取新增或变更的历史会话，并跳过 `memories_update_state.json` 中已处理的内容。
3.  `core/memory_update.py` 按批生成长期记忆草稿；生成期间不修改正式记忆或处理游标。
4.  用户确认后，`core/memory_store.py` 保存对应摘要并创建版本备份，随后才推进全局或该工作区的独立处理游标。

**会话沉淀为 Skill**
1.  用户点击 `沉淀为 Skill`。
2.  用户选择当前会话中的连续消息片段，避免把无关上下文写入 Skill 草稿。
3.  `ConversationSkillDraftWorker` 将选中片段渲染为转录文本，调用 `core/skill_from_conversation.py` 生成草稿，并提取片段内已运行的 `run_python_code` 作为可选脚本资产。
4.  用户选择新建 Skill，或更新已有 Skill 的追加经验/重写说明策略。
5.  保存时写入 `SKILL.md`、`skill.json`、`experience/entries.jsonl`、可选 `impl.py` 与勾选的 `scripts/`/`script_entries`，然后重新加载技能；热加载失败只提示，不中断已保存结果。

**对话内创建 SOP**
1.  用户在输入区 `+` 菜单点击 `从对话生成 SOP`。
2.  `ConversationSopDraftWorker` 将当前会话渲染为转录文本，调用 `core/sop_from_conversation.py` 一次性生成完整 SOP 草稿。
3.  用户在预览对话框中确认生成，或输入修改意见让模型基于上一版草稿重新生成。
4.  确认后保存为任务模板，并通过现有 `create_sop_run()` 绑定到当前会话；后续执行复用 SOP 状态机，但改为应用层逐步派发当前步骤，而不是整段提示词一次性交给模型。单步执行器可为 Agent、上传 Python 文件或 Bash 命令。

**Skill ZIP 导入/导出**
1.  导出时 `SkillManager.export_skill` 定位单个 Skill 目录，将内容压缩为以 Skill 目录名为根的 ZIP；批量导出时 `SkillManager.export_skill_collection` 将多个 Skill 根目录写入同一个集合 ZIP。两种导出都会跳过 `__pycache__`、构建产物等排除目录。
2.  导入时 `SkillManager.import_skill` 接受目录或 `.zip`，ZIP 会先解压到临时目录并校验路径不逃逸。
3.  系统解析平铺根目录或单 Skill 文件夹根目录，从 `skill.json` 或 `SKILL.md` 读取原始名称。
4.  若目标 `ai_skills/<name>` 已存在则拒绝覆盖；否则适配并重新加载技能。

## 5. 分层记忆与上下文处理
- **系统层**：System Prompt 拆成稳定前缀与 runtime context。稳定策略、工具导航、记忆和思考规范保持在请求最前方；工作区、运行模式、日期、runtime 路径、当前工具清单、子 Agent、指定能力和 SOP 当前步骤作为请求尾部临时 system message 注入，不写入持久历史，降低 context cache 前缀失效。
- **记忆层**：`memory/` 保存全局灵魂、全局/工作区摘要与版本备份；旧 `memories.md` 首次使用时非破坏性导入，旧版模块索引、正文和模块备份在布局升级时清理。灵魂与适用摘要自动注入稳定 System Prompt；全局和各工作区使用独立生成游标，确认保存后才更新 `memories_update_state.json`。
- **技能层**：默认只暴露元数据，用户明确选择时注入简版能力提示；完整说明与经验仅在明确选择、`tool_search` 命中或实际技能工具调用后按需注入。
- **会话层**：`run_context` 携带反问模式、指定能力、智能体配置与自动化当前步骤，影响工具可见性与 Prompt 约束。
- **外部工具层**：MCP 配置存储在 `mcp_servers`，兼容 `type = "stdio"` / `type = "streamable_http"` 与 `startup_timeout_ms` 命名，也支持从 `mcpServers` / `mcp_servers` JSON 片段导入；当前只接入 MCP tools，不包含 resources 与 prompts。
- **历史层**：每轮清理/折叠思考内容以避免重复；DeepSeek thinking 工具调用回合保留 `reasoning_content`，避免多轮工具回放触发协议错误。
- **压缩层**：DeepSeek V4 Pro/Flash 默认 `context_window_tokens=1000000`、`context_budget_ratio=0.8`、最近保留 40 轮；压缩切点会避开 assistant/tool 调用回合边界，避免留下孤立 tool result。

## 6. 运行模式与环境

*   **源码模式**：建议使用虚拟环境 **.venv\Scripts\python** 启动。
*   **可执行模式**：PyInstaller 打包后由 `env_utils` 自动定位 Python 与 pip，并默认携带 MCP client 及其运行时依赖。
*   **更新模式**：源码模式只检查 GitHub Releases；可执行模式可下载 ZIP、校验结构、暂存并通过独立脚本关闭旧进程后替换重启。

## 7. 动态技能加载与自我进化
- **更新检测**：对 `SKILL.md`/`impl.py` 的修改时间进行检测，晚于上次加载则触发热加载。
- **热加载**：重置工具注册与提示集合，重新解析并加载实现。
- **经验写回**：通过 `update_skill_experience` 追加经验到 `SKILL.md` 的 `experience` 字段，形成“执行—学习—再执行”的闭环。
- **人工沉淀**：`沉淀为 Skill` 是显式确认通道，会话片段先生成草稿并由用户预览编辑，再写入新 Skill 或更新已有 Skill；已运行 Python 片段可作为脚本入口沉淀。
- **对话生成 SOP**：输入区入口将当前会话提炼为可编辑 SOP 草稿，确认后保存为任务模板并绑定当前会话。
- **SOP 调度执行**：会话与定时自动化都只派发当前步骤；模板默认推进方式可设为人工确认或自动推进，步骤可覆盖模板默认值，完成后由状态机决定暂停、重跑、跳过或继续下一步。等待人工确认的操作入口位于聊天流状态条，右侧抽屉不再承载自动化步骤页。非 Agent 步骤通过沙盒 Python 或 Git Bash 直接执行，并把 stdout/stderr/exit code 写回运行态。
- **迁移复用与调试**：功能中心支持 ZIP 导出/导入，并提供搜索、启用状态筛选、无图标双列轻量列表、Apple 风格滑动开关、选择模式批量导出/删除和能力工作台；能力中心拆分为 `内置能力`、`可选插件`、`MCP`、`自定义能力` 四个 tab，自定义 Skill 可编辑/验证/调试/删除，内置 Skill 只读且不可关闭或删除。开关切换会先更新本地配置和当前列表显示，运行时注册表在下次使用相关能力或手动刷新时统一重载；MCP 开关同步 `mcp_servers[].enabled` 并保留关闭后的列表入口。

## 8. 状态机流转 (Agentic Workflow)
- **状态**：Idle → Thinking → ToolCalling → Observing → Answering → Completed。
- **信号**：`thinking_signal`、`content_signal`、`tool_call_signal`、`tool_result_signal`、`agent_state_signal`。
- **子 Agent 事件**：`agent_state_signal` 在保留状态语义的同时，补充 `input`、`tool_call`、`tool_result`、`content`、`completed` 等结构化字段，供右侧抽屉按节点渲染。
- **子 Agent 生命周期**：模型任务完成时先写入结果与状态，`QThread.finished` 后再释放 worker 引用；若存在排队输入，旧线程完全退出后才启动下一轮，避免线程销毁或重启重叠导致崩溃。
- **子 Agent 诊断日志**：设置 `COWORK_RUNTIME_DEBUG_LOG=1` 后，UI 召唤入口、manager 生命周期、worker signal、pending input、清理与异常会写入 `sub_agent_runtime.log`，位置为 `DeepSeekCowork` 应用数据目录或便携模式的 `user_data/`；日常运行默认关闭高频日志。
- **子 Agent 观测 UI**：状态事件先写入会话事件队列并点亮右侧 `子 Agent` 徽标，不再自动打开抽屉；用户打开抽屉后先清空旧监控视图，再由主线程延迟队列批量渲染轻量摘要行，并限制最近事件窗口，避免事件风暴或 widget 构造影响主任务稳定性。
- **抽屉持续展开策略**：主窗口不再安装用于外部点击关闭的全局 `eventFilter`；文件页、任务观测和子 Agent 共用显式关闭策略。
- **抽屉隐藏诊断**：开启 runtime debug 日志后，`hide_context_drawer` 会记录显式关闭原因和当前 tab；上下文页不再因外部点击自动收起。
- **任务观测安全预览**：右侧 `任务观测` 抽屉只向 Qt 文本控件写入截断后的系统提示词、观测日志和工具详情预览；系统提示词页展示 stable prompt、runtime context 与已披露 skill context，不在首页展示 prompt/tools/message-prefix 指纹。完整 prompt 仍保留在会话状态中且可通过复制按钮导出，避免超长 prompt/JSON 在页面变为可见时触发 native UI 崩溃。
- **交付物预览与转换**：右侧文件页的交付物视图按修改时间展示最近产物，并以会话级 `selected_deliverable_path` 保存当前文件。选择或切回会话时优先复用文件指纹未变化的 HTML、Markdown、图片、PDF 或结构化文档预览；文件更新会标记过期。图片和 DOCX/PPTX/XLSX 预览只读取源文件并生成应用内显示，不修改源文件也不作为新交付物展示。基于 HTML 生成 PPTX/DOCX/PDF 仍由现有 Agent 工具链在当前对话中完成。
- **UI 分段诊断**：开启 runtime debug 日志后，`_handle_agent_state_ui` 会按 session lookup、phase update、tool card、event record、monitor render、bubble PiP、live-agent check、final status 等阶段写入 `ui_agent_state_stage_*` 日志，便于定位 UI 闪退前的最后分支。
- **OpenAI 兼容协议串行化**：父 worker 与子 worker 各自创建独立 provider/client，但进入 OpenAI-compatible `chat_stream` 前会竞争同一协议锁，避免父子 Agent 同时流式请求导致兼容协议或 socket 流混写。
- **Daemon 断流回收**：daemon 流式连接写入失败时会取消交互请求、强制关闭当前会话的 live 子 Agent，并停止主 worker；若 worker 尚未真正退出，daemon 暂存线程句柄到 `detached_workers`，等待 `QThread.finished` 后再清理，避免断流后悬挂或析构运行中的线程。
- **控制**：`pause`、`resume`、`stop`；环路保护（重复思考/工具签名）确保安全收敛。
- **实现要点**：流式解析四类事件；Skill 全文仅沿明确选择、`tool_search` 或实际工具调用链路注入，结果写入历史后继续下一轮直至最终回答。
- **会话自动化状态**：Active → Awaiting Confirmation → Active/Completed，用户可在 Awaiting 状态选择确认、重跑或跳过。
- **定时任务状态**：Enabled/Paused + next_run_at；错过触发时记录为 missed，不自动补跑。cron 语法在应用内解析，不依赖系统 crontab 服务。

## 9. 目录结构

*   **core/**：推理、配置、守护进程、IM 网关等核心逻辑
*   **skills/**：内置系统技能
*   **ai_skills/**：AI 或用户创建技能
*   **ai_skills/quant-strategy-management/**：独立量化策略 Skill，运行产物写入应用数据目录下的专属空间
*   **main.py**：桌面 UI 入口
