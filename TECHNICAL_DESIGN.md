# DeepSeek Cowork 架构设计

项目团队：**deepseek-cowork team**。

当前应用版本：**4.9.1**。

*   **同轮中途引导**：桌面主对话的 `LLMWorker` 维护线程安全的 FIFO guidance 队列，在模型请求前、工具结果返回后及最终收敛边界消费；当前流式响应和正在执行的工具不会被强制打断。daemon 通过 `turn_id`、`turn_started` 与 `steer_message` 协议校验活动轮次，避免迟到输入串入下一轮。引导沿用普通用户消息的 `content` / `content_parts` / 附件元数据，停止或异常时仍保留已接受内容。

## 1. 架构理念

DeepSeek Cowork 采用 **Interleaved Chain-of-Thought** 架构，在推理阶段直接调用工具，实现“思考-执行-再思考”的闭环，降低幻觉并提升任务成功率。

## 2. 核心组件

### 2.1 UI 层 (PySide6)
*   **main.py**：桌面入口，负责窗口、聊天气泡、工具调用卡片、右侧上下文抽屉等 UI 交互，并根据窗口与抽屉状态动态计算主会话区宽度。
*   **项目式左侧栏**：顶部入口创建无项目纯对话，项目行 `+` 创建项目会话；`+` 由 Qt 绘制以避免图标字体在部分系统或 DPI 下失效，Tooltip 同时通过样式表和 Palette 固定为浅色。项目标题只展开/收起会话预览，当前项目、文件树和交付物面板始终跟随当前可见会话。
*   **会话级工作区边界**：`SessionState.workspace_dir` 是运行与持久化的工作区来源；激活无项目会话会清除旧项目上下文。`run_context.workspace_mode` 区分 `chat_only` 与 `project`，工具注册根据 handler 是否接收 `workspace_dir` 标记依赖，并在纯对话的工具列表和发现结果中统一过滤。连接项目会保留消息并写入 `meta.workspace_dir`。
*   **右侧上下文抽屉**：文件、交付物、任务观测、子 Agent 监控以隐藏抽屉承载；展开时以抽屉左边界作为主阅读区的安全边界，避免遮挡对话。抽屉宽度、主对话列宽度与左右留白现在统一由同一套三栏布局规则计算，并通过 `content_area_layout` 右侧预留空间保证切换文件 / 交付物 / 观测 / 子 Agent tab 或进入其子界面时几何稳定。交付物页扫描工作区 HTML、图片、PDF、DOCX、PPTX 和 XLSX 输出，HTML 通过延迟加载的 QtWebEngine 内嵌渲染，环境不可用时回退为文本预览和系统打开。
*   **动态对话阅读列**：消息列表与输入栏会根据主窗口可用宽度、右侧抽屉开合状态和保底留白动态计算；关闭抽屉时主对话列居中，打开抽屉时主对话列按三栏比例左移，并同步更新消息区、用户气泡、输入卡片和系统提示条宽度，避免输入框、消息卡片和 drawer 子界面出现忽宽忽窄的跳变。
*   **会话工具栏**：添加文件、智能体提及、自动化模板绑定、指定能力、反问模式统一从输入区入口触发。
*   **设置中心**：设置弹窗采用更接近 Apple 桌面偏好设置的左侧导航 + 右侧内容区结构，内容区使用轻量无边框分区；常规文案偏产品表达，MCP 相关术语保持英文。
*   **对话分支按钮**：已完成的用户/助手气泡会暴露 `分支` 图标按钮，点击后创建一个新的线性会话快照；用户消息额外提供“编辑后重新生成”和“删除并继续”，两者都通过新分支承载，不在同一会话内改写历史。
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
*   **异步会话持久化**：`save_chat_history()` 只在 UI 线程里整理快照并入队，后台 `ChatSaveWorker` 按会话合并、500ms debounce 后写入 SQLite；分支、长期记忆更新、会话重命名/归档/删除和应用退出前显式 flush，避免异步保存带来的读取时序问题。
*   **UI 到 Daemon 的上下文快照**：桌面会话交给 daemon 执行时会随请求传递当前 `state.messages` 快照；daemon 优先用该快照刷新内存会话，再追加本轮用户消息，避免 idle suspend 后从旧 SQLite 或旧缓存恢复导致上下文漂移。
*   **历史加载一致性屏障**：异步恢复会话期间，输入和保存请求均被拦截；加载完成后通过 `set_current_session()` 重新绑定窗口消息别名。历史迁移版本 3 会移除旧 query 模糊匹配生成的隐藏 Skill 上下文，同时保留 `tool_search` 和真实工具调用产生的上下文。
*   **运行时诊断日志开关**：高频子 Agent/UI runtime 日志默认关闭，仅当 `COWORK_RUNTIME_DEBUG_LOG=1` 时写入 `sub_agent_runtime.log`，避免状态流和磁盘 IO 绑定。
*   **UI 异常持久化**：`SafeApplication.notify(...)` 保留全局事件保护，但捕获异常时会将接收控件类型、事件类型和完整 traceback 始终追加到应用数据目录（便携模式为 `user_data/`）下的 `ui_error.log`；系统提示仅指向日志，不再把通用“继续运行”文案当作错误详情。
*   **反馈回路按钮**：侧边栏 `更新长期记忆` 与 `沉淀为 Skill` 触发后台 worker，并在 UI 中提供进度、预览、编辑与保存确认。

### 2.2 Agent Core
*   **core/agent.py**：推理循环与工具调度，负责将用户输入转化为可执行任务。
*   **core/interaction.py**：桥接 UI 与推理流程，统一消息与工具调用格式。
*   **core/mcp_client.py**：封装 MCP `stdio` 与 Streamable HTTP 会话，负责连接测试、工具枚举与工具调用；对 `mcp` Python client 的新旧 Streamable HTTP API 做版本兼容。
*   **core/llm/providers.py**：在 OpenAI-compatible / Anthropic provider 边界把 `input_image` 转换成 base64 data URL 视觉块；未开启 `supports_vision` 时仅保留文本提示，因此 OCR 走模型能力而不额外引入本地 OCR 引擎。OpenAI-compatible provider 会请求并解析 streaming usage 中的 cached token 细节；会话级 prompt cache key 仅在模型配置显式声明参数名时透传。
*   **core/sandbox_runtime.py**：解析 bundled Python / Node.js / Git Bash，Windows 打包版优先直接使用当前应用目录的 `_internal/*_env` 结构，并为沙盒命令注入对应 PATH；AppData `runtime_sandbox` 仅作为临时、缓存和 skill 依赖根目录。若发现 `python_env/python.exe` 只是依赖外部解释器的 venv redirector，会在运行时标记为不可用而不是继续误报；`bash` 执行层在 Windows 缺失 Git Bash 时退回 `cmd.exe`。Skill 级 Python 依赖统一安装到 `runtime_sandbox/.../skills/<skill>/python/site-packages`，由沙盒 `PYTHONPATH` 注入；同时生成 bootstrap `sitecustomize.py`，并通过 `PATH` 与 `COWORK_PYTHON_DLL_DIRS` 暴露 bundled runtime 和 skill 目录中的原生 DLL 搜索路径。
*   **core/env_utils.py**：`ensure_package_installed(...)` 不再只依赖主进程 `importlib` 判断是否已安装，而是用沙盒 Python 直接验证目标模块可导入。对于 `python-runner`，若依赖状态缓存显示已安装但沙盒实际无法导入，会强制重装一次以修复失真的缓存记录；若最终失败，则把沙盒 traceback 回传，便于定位 `ImportError` / DLL load failure。
*   **core/process_utils.py**：集中提供 Windows 无控制台窗口的 subprocess 参数、进程单例锁和 runtime debug 日志开关，供 UI、updater、沙盒和系统技能复用，避免新增执行入口再次闪出 CMD。
*   **deepseek-cowork.spec**：内置 `python_env` 除 `Lib/` 和最小 `site-packages` 外，还要包含 Windows `DLLs/` 或同类平台扩展目录，以及常见 MSVC runtime DLL；否则 `_socket`、`_ssl` 一类标准扩展缺失，或 native wheel 在 `_internal/python_env` 中无法加载。

### 2.3 Daemon 与并发
*   **core/daemon.py**：无头推理服务，分离 UI 与模型推理负载。
*   **QThread**：UI 与后台线程解耦，保证界面响应。

### 2.4 技能系统
*   **core/skill_manager.py**：加载 `skills/` 与 `ai_skills/`，注入工具定义与经验。
*   **独立 Skill 托管**：`ai_skills/quant-strategy-management` 作为完整能力包接入，内部自带 Strategy DSL、存储、数据拉取、回测、报告和 CLI 入口，不依赖外部工程目录。
*   **经验回写**：执行结果可回写到 `SKILL.md`，形成自进化闭环。
*   **能力包迁移与调试**：支持从单个 Skill 目录、Skill 集合目录或 ZIP 导入 Skill，支持将已有 Skill 单独导出或多选导出为集合 ZIP，并跳过缓存/构建目录；能力工作台和批量删除限制在 `ai_skills`，可验证 Skill 文件并调试 tools/scripts。
*   **显式只读并行**：`parallel_tools` 通过 `SkillManager.call_tool(..., require_read_only=True)` 执行子调用，保留顺序并遵守发现、模式和能力范围限制。
*   **core/skill_from_conversation.py**：把当前会话转录为可复用 Skill 草稿，并负责新建或更新 Skill 文件。

### 2.5 自动化、配置与存储
*   **core/sop_manager.py**：规范化自动化模板和会话运行态，维护 step/run 状态、步骤执行器元数据，并生成当前步骤 Prompt 片段。
*   **core/automation_manager.py**：规范化定时任务、计算 cron / 快捷计划的 next run、生成完整执行提示词并维护运行历史记录结构。
*   **core/config_manager.py**：统一配置入口，管理 API Key、Provider、`mcp_servers`、项目列表、工作区、自动化任务与运行历史。
*   **core/chat_storage.py**：历史对话持久化，按 `meta.workspace_dir` 支持项目分组、无项目对话查询和项目会话归档；会话可通过 `meta.conversation_branch` 记录来源会话、来源消息和分支动作类型。SQLite 连接启用 WAL / busy timeout，并在普通追加路径下只写入新增消息，编辑、删除和迁移仍回退全量重写。旧版 `chat_history_*.json` 默认不再参与侧边栏刷新，而是通过手动迁移写回 SQLite。
*   **core/memory_update.py**：扫描历史会话，分批更新 `memories.md`，写入备份与 `memories_update_state.json`。
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
1.  用户点击 `更新长期记忆`。
2.  `MemoryUpdateWorker` 从 `core/chat_storage.py` 读取新增或变更的历史会话，并跳过 `memories_update_state.json` 中已处理的内容。
3.  `core/memory_update.py` 按批构造提示，将当前 `memories.md` 与历史批次交给模型合并；当模型返回为空或失败时执行有限重试。
4.  每个批次写入 `memories.md` 并生成备份，同时向对话框回传进度和批次预览；最终结果仍允许用户编辑并再次保存。

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
- **记忆层**：`memories.md`（可选）承载稳定偏好与长期信息，自动注入 System Prompt；`更新长期记忆` 通过 `memories_update_state.json` 记录处理进度，后续运行聚焦新增或变更会话。
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
- **抽屉点击命中保护**：`eventFilter` 对右侧抽屉和上下文 rail 采用全局坐标命中判断，而不是只依赖 Qt 祖先链；因此滚动区域 viewport、内部子控件和临时弹层不会被误判为抽屉外点击。
- **抽屉隐藏诊断**：开启 runtime debug 日志后，`hide_context_drawer` 会把关闭原因、来源控件类型、当前 tab 和命中判断写入 `sub_agent_runtime.log`，便于定位“点击后立即收起”类问题。
- **任务观测安全预览**：右侧 `任务观测` 抽屉只向 Qt 文本控件写入截断后的系统提示词、观测日志和工具详情预览；系统提示词页展示 stable prompt、runtime context 与已披露 skill context，不在首页展示 prompt/tools/message-prefix 指纹。完整 prompt 仍保留在会话状态中且可通过复制按钮导出，避免超长 prompt/JSON 在页面变为可见时触发 native UI 崩溃。
- **交付物预览与转换**：右侧 `交付物` 抽屉按修改时间列出工作区产物；HTML 支持渲染、刷新、打开、显示位置和复制路径。用户选择 HTML 后点击生成 PPTX/DOCX/PDF 时，UI 会将源 HTML 附加到当前对话并继续提交生成任务，由 AI 使用现有工具链生成办公文件，而不是新建对话或在抽屉内写死转换逻辑。
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
