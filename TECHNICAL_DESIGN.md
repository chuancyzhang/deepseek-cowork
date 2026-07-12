# DeepSeek Cowork 技术设计

当前应用版本：**4.9.9**

## 1. 设计目标

DeepSeek Cowork 的目标不是做一个“会聊天的 IDE”，而是做一个桌面 Agent 工作台：

- 用单轮内交错式推理和工具调用完成真实任务
- 把本地文件操作约束在对话工作目录或项目工作区边界内
- 用技能系统提供经验，而不是引入第二套执行协议
- 用桌面 UI 承载观察、确认、交付物预览和自动化管理

## 2. 总体架构

系统由五层组成：

1. **UI 层**
   `main.py` 提供对话界面、项目侧栏、右侧上下文抽屉，以及 `conversation / capabilities / automation / settings` 主内容区页面路由；`core/theme.py` 的 `DesignTokens` 是颜色、状态、排版和基础几何的唯一主题来源，`ui/primitives.py` 提供页头、工具栏、分段控件、主从容器、状态提示、数据行和固定操作栏等共享组件。
2. **Agent 层**
   `core/agent.py` 负责推理循环、工具调度、模式约束和结果汇总。
3. **能力层**
   `core/skill_manager.py` 负责加载 `skills/`、`ai_skills/`、MCP 工具，并按需向模型披露技能内容。
4. **运行层**
   `core/daemon.py`、`core/sandbox_runtime.py` 与相关 provider 代码负责模型请求、后台执行和运行时环境。
5. **数据层**
   `core/chat_storage.py`、`core/config_manager.py`、`core/memory_store.py`、`core/automation_manager.py` 负责会话、配置、记忆和自动化状态持久化。

## 3. 关键界面结构

- **项目式对话**：直接对话默认绑定 exe 运行目录下的 `conversation_workspaces/<session_id>/` 独立工作目录；项目对话只在用户显式绑定空白会话或从项目行新建会话时写入 `workspace_source="project"`。打开、添加、拖入或浏览项目只更新全局文件视图和项目列表，不反向修改已有会话的项目归属；左侧项目导航使用轻量图标按钮承载添加、排序和项目操作。
- **首页工具包提示**：新会话空态提示用户可在设置的“组件与依赖”中安装文档工具包和数据分析工具包，帮助用户在文档、表格和数据分析任务前发现可选依赖。
- **生成办公稿**：AI 回复末尾的消息级动作，按自由、PPT、设计稿、DOCX 注入办公交付策略，并把该生成轮次默认折叠为任务卡；结果文件入口保持外显。
- **PPT Mode / PPT Agent**：新会话空态提供 PPT Agent 卡片，输入工具栏 Agent 选择器承载内置/自定义智能体；`PptAgentModeDialog` 仅承担 PPT 需求这一原子事务，提交后仍进入 `office_html_first` 工作流。
- **右侧上下文抽屉**：文件、交付物、任务观测、子 Agent 状态默认隐藏。文件浏览首页由 SQLite 交付物索引驱动：有明确产物时先显示交付物并按需切到工作区，无产物时直接显示目录树；聊天结果、办公转换和用户标记负责注册索引，普通扩展名扫描不再决定交付物。浏览态和详情态之间保留选中路径、筛选、目录展开项和滚动位置。
- **设置中心**：作为主内容区页面，以紧凑分类导航集中配置模型、智能体、个性与记忆、工作区、MCP、企业消息、更新和运行组件；用户尚未创建智能体时显示的“新智能体”模板默认停用，需完成配置并主动启用后才进入可用智能体列表；脏状态比较规范化后的可保存快照，记忆与配置失败时共同回滚。
- **自动化中心**：作为主内容区页面，以分段控制、任务列表、页面内任务编辑和执行详情承载提示词任务、引用能力、Agent 绑定、计划任务和执行历史；每个任务独立保存。

UI 采用三层表面模型：应用画布、功能面板、控件或数据行。共享 QSS 必须通过明确控件类型、`objectName` 或动态属性限定作用域，避免父级边框和背景继承到子控件；普通控件保持 `30-32px` 高度和 `6-8px` 圆角。`ProductTooltipController` 拦截 QWidget 与 item view 的提示事件，在当前顶层窗口内绘制提示，避免 Windows 原生 Tooltip 黑窗。所有交互控件必须提供 hover、pressed、focus、disabled 等状态。

`ui/primitives.py` 提供统一消息确认与文本输入弹层，业务层的 `QMessageBox/QInputDialog` 兼容入口均路由到这些应用内表面；Windows 原生 `QFileDialog` 保留。全局 Toast 在主窗口右下安全区最多堆叠三条，支持去重计数、手动关闭和悬停暂停。交付物分类由 `DELIVERABLE_TYPES` 的 kind 映射派生，排序以规范化相对路径作为稳定次级键。文件或目录定位统一调用 `core.process_utils.reveal_path_in_file_manager()`，Windows 通过 ShellExecute 显示 Explorer，不得复用隐藏控制台窗口参数。

`scripts/render_user_guide_screenshots.py` 使用隔离临时配置生成用户指南中的真实 PySide6 截图，并在 `1280x720`、`1440x900`、`1920x1080` 下检查输入区与右侧抽屉不重叠；HTML 截图依赖 `requirements.txt` 中声明的 `PySide6-Addons`，缺失时直接报错。

这套结构的核心目的是把“执行过程”和“产品操作”留在同一个窗口内完成，而不是依赖外部终端或浏览器页面。

启动时先显示轻量准备窗口，再通过 `QTimer.singleShot(0, ...)` 构造主窗口；只有 `MainWindow` 初始化完成并发出显示请求后才关闭准备窗口。主窗口构造期只做必要控件、配置、会话保存队列和初始会话初始化；默认工作区加载、历史首屏刷新和后台服务启动放到显示后的 hydration 与后台任务阶段，避免启动遮罩被长任务卡住。Skill 初始化在主窗口显示后由后台 worker 构建完整 `SkillManager`，启动期只保留轻量发现入口；MCP server 先注册为能力元数据，具体 `list_tools()` 等到 `tool_search`、MCP 调试或调用路径再执行。交付物 WebEngine 预览延迟到首次 HTML/Markdown/Office 预览时创建；历史侧栏默认只取最近一页摘要，用户需要时再继续加载更多记录。

右侧上下文徽标、澄清状态和抽屉提示属于同一组 UI 状态，但刷新方向必须保持单向：`refresh_context_badges()` 可以调度局部控件同步，局部控件刷新函数不能反向触发全量上下文刷新。移除或重构控件时要同步清理旧刷新链路，并用当前会话存在的场景覆盖递归回归测试。

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
- `skill.json` 可声明 `config_fields`；配置保存到本地 `skill_configs`，运行脚本或工具时按字段声明显式注入环境变量。需要生成 MCP server 的能力可声明 `mcp_server_presets`，由 `SkillManager` 使用已保存配置渲染 `stdio` 或 Streamable HTTP server，并通过配置管理器按 server ID upsert，避免重复生成。
- 标准 Agent Skill 安装保留上游根目录 `SKILL.md`，由系统生成 `skill.json` 作为本地检索、能力工作台和调试索引
- 模型选择是对话级的下一轮输入参数，不是底层全局运行态；UI 提交时把当前对话的 `selected_model_id` 和完整 `selected_model_profile` 写入 `run_context`，本地 worker、daemon 和子智能体均优先使用该快照创建 provider。运行中切换模型只更新会话下一轮选择，不会影响已启动流程。
- Composer 使用 `ProductPopover` 作为局部选择面的统一容器：`+` 动作、指定能力和模型选择都锚定到触发控件，支持键盘关闭与焦点恢复，不再依赖原生 `QMenu` 或大型阻塞选择弹窗。指定能力以会话态 `selected_skill_names` 为唯一数据源；Popover 内的清除、取消和应用保持事务性 dirty state。
- Thinking 继续持久化原始 reasoning 和工具消息结构，只把折叠态压缩为过程行。展开态按同一工具调用 ID 串联 reasoning、工具摘要和右侧任务观测；历史恢复不重放流式动画，当前进程内记录用户主动展开状态。
- 生成办公稿使用本次请求级 `workflow_mode = office_html_first` 和 `office_output_profile` 注入提示；从 HTML 继续生成 PPTX、DOCX、PDF 使用 `workflow_mode = office_file_conversion` 和 `office_conversion_target` 标记；两者都不新增 `RUN_MODE`，因此不改变工具权限。
- PPT Agent 是 `office_html_first + office_output_profile = ppt` 上的一层产品工作流；`core/ppt_agent.py` 注册默认 PPT Agent、Guizang PPT Skill、Frontend Slides、Huashu Design 等 html-ppt 策略，并根据显式选择、偏好、模板和关键词自动选择策略。后三者作为真实 `ai_skills` 包内置，PPT Agent 提交时会把选中的策略映射为临时 `selected_skill_names`，让上游 `SKILL.md`、引用和资源说明进入本轮系统上下文与任务观测页，但不会写回用户会话的手动技能选择。`core/agent.py` 通过 `ppt_agent_mode`、`ppt_agent_strategy`、`ppt_agent_selected_strategy`、`ppt_agent_preference` 和 `ppt_agent_template_file` 注入 PPT Agent 运行提示，但仍要求最终输出 HTML deliverable。
- UI 通过用户消息 `meta.workflow_mode` 将该轮用户消息、工具调用和助手结果渲染成可展开的办公任务卡；消息本体仍按原结构持久化，交付物文件卡渲染在折叠过程外。任务卡会合并最终回复、隐藏气泡识别到的文件卡路径和本轮工具变更中的有效工作区文件，避免最终回复未写完整路径时结果区为空；消息渲染会把当前工作区内的裸完整路径和 Markdown 文件链接统一重写为 `cowork-file:`，再交给右侧文件预览链路处理。任务刚提交时会写入用户请求、附件、选中 Skill 和启动阶段节点；收到系统提示词、Skill 上下文、thinking、工具调用和工具结果后继续追加到过程区，避免展开过程只显示占位。若 live 提交后短时间内过程区仍为空，UI 会补充“等待模型运行接管”的可见状态；daemon 流与普通对话保持一致，不设置单独的模型响应超时，只在 worker 完成、显式失败、用户停止或客户端断开时结束。PPT Agent/办公任务提交链路会写入 `ppt_agent_debug.log`，daemon 流会写入 `daemon.log`，用于定位卡在 UI 提交、任务卡创建、run context 构建、daemon 分发还是后台 worker 启动阶段。历史消息按 render span 独立渲染，避免普通消息和办公任务混排时丢失折叠状态。
- 运行中引导使用用户消息 `meta.same_turn_guidance` 标记，通过 steer 注入当前 in-flight turn；UI 将其渲染为当前任务流里的内联“补充引导”片段，而不是新的用户气泡或新的对话轮次。
- `request_user_input` 与审批请求由 `InlineInteractionCard` 渲染在发起会话内，并通过原有 resolver/daemon response 通道返回；后台会话只更新侧栏状态和 Toast。日志仅记录 request ID、类型、会话和完成状态，不记录问题正文或用户回答。
- 用户添加的文件同时保留 UI 附件元数据和模型可见内容：剪贴板图片写入 `chat_history_dir/attachments/<session_id>/` 并以缩略图展示，在支持视觉的模型中转为图片 part；不支持视觉时提交预检会阻止发送并保留输入。小体积文本附件内联，大文件或非文本文件只提供明确路径、大小和工具读取提示。
- 从 HTML 生成 PPTX 时可附加 PPTX 模板文件，提示要求以 HTML 为内容源、模板为视觉结构源，并保留模板主题、母版、字号、色彩、版式节奏和顶部/底部图片；UI 以“生成文件…”菜单选择 PPTX、DOCX 或 PDF，选择 PPTX 后再按需询问模板。转换提交后只更新交付物详情页底部的局部运行态，并用隔离运行上下文只传入源 HTML、目标格式和模板文件，不把上一轮办公稿生成过程带给模型；完成后仍通过任务卡、Toast 和交付物扫描回填结果。`main.py` 的 `_submit_html_deliverable_conversion` 是可复用提交入口，右侧交付物预览和后续 PPT Mode 都应复用它，不另建转换器。
- `tool_search` 负责延迟发现工具与匹配技能
- `parallel_tools` 只允许并行只读调用
- 同一轮内如果模型连续 3 次请求完全相同的工具签名，Agent 会停止该轮并提示重复工具调用，避免工具结果已返回后继续空转。

## 5. 技能与工具

能力来源有四类：

- `skills/`：核心内置技能
- `ai_skills/`：随包可选插件与用户技能
- 标准 Agent Skill：通过 `install_agent_skill` 写入用户级 `ai_skills`，保留原始 `SKILL.md`
- MCP servers：通过 `stdio` 或 Streamable HTTP 接入的外部工具；ShowDoc MCP、Airflow MCP 与官方 Superset MCP 可由对应内置 skill 的配置页生成默认关闭的 server 条目
- 常驻执行工具：例如 `run_python_code`

设计原则：

- 工具负责执行
- 技能负责经验
- 技能按需披露，默认只给最小摘要
- 技能运行配置由工作台表单维护，缺少必填配置时直接失败，不走静默降级
- 用户可以通过“沉淀为 Skill”把会话经验转为可复用能力；目标 Skill 使用可搜索选择器，会话片段支持当前问答、最近三轮、全部或自定义。草稿后台生成后保留在会话任务行中，只有用户主动查看并保存才进入能力中心数据源。

详细模型见 [SKILL_SYSTEM.md](SKILL_SYSTEM.md)。

## 6. 自动化设计

自动化由 `core/automation_manager.py` 统一管理计划任务、下次执行时间和运行历史。任务数据只保留：

- `prompt`：触发时提交给当前会话或 Agent 的执行提示词
- `skill_names`：本次自动化显式引用的能力
- `agent_profile_id`：可选的 Agent 绑定；为空时由主助手执行
- 计划字段：每日、每周、每月、间隔、一次性、cron 等调度配置

特点：

- 自动化不再维护步骤模板或会话推进状态
- 触发时直接提交任务提示词，并把引用能力写入运行上下文
- 绑定 Agent 时通过子 Agent 调度路径执行，运行历史记录 Agent 名称和状态

## 7. 数据与持久化

- **会话**：`core/chat_storage.py` 负责当前格式本地消息历史、归档、置顶和项目归属；项目分组只信任会话自身 meta 中的 `workspace_dir/workspace_source`，`workspace_source="chat"` 的独立对话工作目录始终作为普通对话展示，也不会被项目批量归档命中。左侧历史导航会合并当前内存态中已提交的会话，让新对话在后台保存队列落库前立即可见，保存完成后再与 SQLite 记录校准。UI 激活历史会话前会 flush 同会话保存队列，读取失败保持 `history_loaded=False` 并显示错误占位，避免把失败状态写成空历史；编辑或删除历史消息后仍按历史分页窗口渲染，避免把整段历史一次性重放。早期文本文件历史不再进入当前加载兼容链路。
- **配置**：`core/config_manager.py` 统一管理模型、MCP、Skill 运行配置、工作区、智能体和 UI 偏好；项目归档保存在项目元数据中，左侧栏和项目选择器默认过滤已归档项目，设置中心负责恢复入口
- **记忆**：`core/memory_store.py` 与 `core/memory_update.py` 管理灵魂提示词、全局摘要和工作区摘要
- **技能**：文件系统中的 `SKILL.md`、`skill.json`、`impl.py`、`experience/entries.jsonl`；自动工具发现披露的技能全文是本轮运行时上下文，历史迁移和响应合并会过滤这些隐藏 system 消息，避免把临时技能材料带入后续请求的 prompt cache 前缀
- **自动化**：提示词任务、引用能力、Agent 绑定、计划任务和执行历史保存在本地配置数据中；旧 SOP 模板配置会在迁移时清空，旧模板任务会停用保留并提示用户补充提示词

## 8. 运行环境

- 桌面 UI 基于 PySide6
- 模型接入支持 OpenAI-compatible 与 Anthropic
- 打包模式保留 Python 基础运行时和 Git Bash
- Node.js、文档读取、浏览器自动化和金融数据等依赖改为设置中的可选组件；内置金融 Skill 保持数据查询边界，不再默认内置策略回测能力
- 打包版内置 MCP client 运行时，便于直接接入 MCP tools

## 9. 设计取舍

- **优先产品化桌面体验**：把工具链、观察和交付物预览做进 UI，而不是只暴露底层能力。
- **优先单一执行面**：不让 skill、automation、memory 各自演化成新的执行协议。
- **优先显式边界**：工作区、模式、人工确认和技能披露都尽量显式可见。
- **优先可演进性**：用户技能、MCP 工具、提示词自动化和长期记忆都可以持续扩展。
- **会话显示标题**：自动标题继续由首条用户消息生成；用户在侧栏就地重命名后，conversation `meta.manual_title` 标记标题来源，后续异步保存、历史迁移和重新加载必须保留该标题，且不修改数据库表结构。
- **任务观测展示**：`ProductSegmentedControl` 承载执行上下文、调用记录和技术详情；调用记录是工具选择源，技术详情使用共享代码与结果查看器按 Python、Shell、JSON、stdout、stderr 和 Traceback 呈现。
- **UI 导航诊断**：侧栏新建聊天、项目点击、输入栏项目切换和主内容路由向 `ui_navigation.log` 写入 begin/done 阶段；正式 UI 进程通过 `faulthandler` 把原生崩溃线程栈写入 `native_crash.log`。

## 10. 代码入口

- `main.py`
- `core/agent.py`
- `core/daemon.py`
- `core/skill_manager.py`
- `core/mcp_client.py`
- `core/automation_manager.py`
- `core/chat_storage.py`
- `core/config_manager.py`

如果需要产品视角的说明，见 [PRODUCT_DOC.md](PRODUCT_DOC.md)；如果需要快速上手，见 [README_CN.md](README_CN.md)。
