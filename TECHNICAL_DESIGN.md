# DeepSeek Cowork 技术设计

当前应用版本：**5.0.3**

本设计以 5.0.3 发布基线为准；面向用户的范围、兼容性和验收清单见 [RELEASE_NOTES_5.0.3.md](RELEASE_NOTES_5.0.3.md)。

## 1. 设计目标

DeepSeek Cowork 的目标不是做一个“会聊天的 IDE”，而是做一个桌面 Agent 工作台：

- 用单轮内交错式推理和工具调用完成真实任务
- 把本地文件操作约束在对话工作目录或项目工作区边界内
- 用技能系统提供经验，而不是引入第二套执行协议
- 用桌面 UI 承载观察、确认、交付物预览和自动化管理

## 2. 总体架构

系统由五层组成：

1. **UI 层**
   `main.py` 提供对话界面、项目侧栏、右侧上下文抽屉，以及 `conversation / capabilities / automation / settings` 主内容区页面路由；`core/theme.py` 的 `DesignTokens` 是颜色、状态、排版和基础几何的运行时主题来源，`core/theme_service.py` 负责与 Qt 解耦的主题校验、解析和文件仓库，`ui/primitives.py` 提供页头、工具栏、分段控件、主从容器、状态提示、数据行和固定操作栏等共享组件。
2. **Agent 层**
   `core/agent.py` 负责推理循环、工具调度、模式约束和结果汇总。
3. **能力层**
   `core/skill_catalog.py` 在 UI 与 daemon 进程内分别维护原子替换的不可变 Skill 目录快照；`core/skill_manager.py` 提供会话隔离的轻量运行视图、工具披露与调用。
4. **运行层**
   `core/daemon.py`、`core/sandbox_runtime.py` 与相关 provider 代码负责模型请求、后台执行和运行时环境。
5. **数据层**
   `core/chat_storage.py`、`core/config_manager.py`、`core/memory_store.py`、`core/automation_manager.py` 负责会话、配置、记忆和自动化状态持久化。

## 3. 关键界面结构

- **项目式对话**：直接对话默认绑定 `ConfigManager.get_chat_workspace_root()/<session_id>/` 独立工作目录，根目录默认使用应用数据目录并可在设置中心修改；设置变更只影响新建或尚未绑定目录的会话，已有会话不静默搬迁。项目对话只在用户显式绑定空白会话或从项目行新建会话时写入 `workspace_source="project"`。打开、添加、拖入或浏览项目只更新全局文件视图和项目列表，不反向修改已有会话的项目归属；左侧项目导航使用轻量图标按钮承载添加、排序和项目操作。
- **首页工具包提示**：新会话空态提示用户可在设置的“组件与依赖”中安装文档工具包和数据分析工具包，帮助用户在文档、表格和数据分析任务前发现可选依赖。
- **生成办公稿**：AI 回复末尾的消息级动作，按自由、PPT、设计稿、DOCX 注入办公交付策略，并把该生成轮次默认折叠为任务卡；结果文件入口保持外显。
- **PPT Mode / PPT Agent**：新会话空态提供 PPT Agent 卡片，输入工具栏 Agent 选择器承载内置/自定义智能体；`PptAgentModeDialog` 仅承担 PPT 需求这一原子事务，提交后仍进入 `office_html_first` 工作流。
- **右侧上下文抽屉**：文件、交付物、任务观测、子 Agent 状态默认隐藏。文件浏览首页由 SQLite 交付物索引驱动：有明确产物时先显示交付物并按需切到工作区，无产物时直接显示目录树；聊天结果、办公转换和用户标记负责注册索引，普通扩展名扫描不再决定交付物。真实空目录根据 `workspace_source` 区分聊天目录与项目目录文案，隐藏搜索/筛选并提供打开实际目录的操作；筛选无结果仍保留恢复控件。浏览态和详情态之间保留选中路径、筛选、目录展开项和滚动位置。
- **子 Agent 观测边界**：子 Agent 事件继续保存到会话状态、右侧观测页和诊断日志；普通对话气泡不创建 Agent 短 ID、状态 PiP 或原始日志容器，只接收需要回传的最终结果。
- **设置中心**：作为主内容区页面，以紧凑分类导航集中配置外观、模型、智能体、个性与记忆、工作区、MCP、企业消息、更新和运行组件；外观页只把内置浅色主题作为只读基线，用户主题使用独立 JSON 文件并参与统一脏状态、预览和回滚；主题文件持久化成功后不因当前 Qt 界面刷新失败而撤销，设置页会明确提示重启载入，其他配置或持久化步骤失败时仍统一回滚；用户尚未创建智能体时显示的“新智能体”模板默认停用，需完成配置并主动启用后才进入可用智能体列表；脏状态比较规范化后的可保存快照，记忆与配置失败时共同回滚。
- **自动化中心**：作为主内容区页面，以分段控制、任务列表、页面内任务编辑和执行详情承载提示词任务、引用能力、Agent 绑定、计划任务和执行历史；每个任务独立保存。

UI 采用三层表面模型：应用画布、功能面板、控件或数据行。共享 QSS 必须通过明确控件类型、`objectName` 或动态属性限定作用域，避免父级边框和背景继承到子控件；普通控件保持 `30-32px` 高度和 `6-8px` 圆角。`ProductTooltipController` 拦截 QWidget 与 item view 的提示事件，在当前顶层窗口内绘制提示，避免 Windows 原生 Tooltip 黑窗。所有交互控件必须提供 hover、pressed、focus、disabled 等状态。

`ui/primitives.py` 提供统一消息确认与文本输入弹层，业务层的 `QMessageBox/QInputDialog` 兼容入口均路由到这些应用内表面；Windows 原生 `QFileDialog` 保留。全局 Toast 在主窗口右下安全区最多堆叠三条，支持去重计数、手动关闭和悬停暂停。交付物分类由 `DELIVERABLE_TYPES` 的 kind 映射派生，排序以规范化相对路径作为稳定次级键。文件或目录定位统一调用 `core.process_utils.reveal_path_in_file_manager()`，Windows 通过 ShellExecute 显示 Explorer，不得复用隐藏控制台窗口参数。

`scripts/render_user_guide_screenshots.py` 使用隔离临时配置生成用户指南中的真实 PySide6 截图，并在 `1280x720`、`1440x900`、`1920x1080` 下检查输入区与右侧抽屉不重叠；HTML 截图依赖 `requirements.txt` 中声明的 `PySide6-Addons`，缺失时直接报错。

这套结构的核心目的是把“执行过程”和“产品操作”留在同一个窗口内完成，而不是依赖外部终端或浏览器页面。

启动时先显示轻量准备窗口，再通过 `QTimer.singleShot(0, ...)` 构造主窗口；只有 `MainWindow` 初始化完成并发出显示请求后才关闭准备窗口。主窗口构造期只做必要控件、配置、会话保存队列和初始会话初始化；默认工作区加载、历史首屏刷新和后台服务启动放到显示后的 hydration 与后台任务阶段，避免启动遮罩被长任务卡住。Skill 初始化在主窗口显示后由后台 worker 构建进程级目录快照；请求 Worker 只克隆运行视图，不执行 `load_skills()`。声明式 `skill.json.tools` 记录名称、参数 schema、权限和 `impl.py:function` 绑定，处理器延迟到首次调用才导入；旧反射 Skill 按实现哈希复用模块。MCP server 先注册为能力元数据，具体 `list_tools()` 等到 `tool_search`、MCP 调试或调用路径再执行。交付物 WebEngine 预览延迟到首次 HTML/Markdown/Office 预览时创建；历史侧栏默认只取最近一页摘要，用户需要时再继续加载更多记录。

Skill 变更统一使用 `SkillChangeEvent`（事件 ID、动作、Skill 名称、来源、会话和修订号）。能力中心启停、导入、删除、会话沉淀以及 AI 创建/更新都先完成校验与原子目录发布，再让 UI 与 daemon 分别重建快照；重复事件幂等，旧修订不会覆盖新快照。运行中工具不被中断，Worker 只在下一模型请求边界调用 `apply_snapshot()`。根目录后台监听只修复外部编辑，不参与强一致业务链路。

`DependencyCoordinator` 在首次工具调用前按 Skill 与依赖哈希执行 single-flight。默认超时 300 秒，配置项 `skill_dependency_install_timeout_seconds` 被限制在 30–1800 秒；成功与失败都持久化，失败不会随新对话自动重试，只能由能力中心显式重试或依赖声明变化触发。安装 start/finish/error、快照构建/切换和工具执行均进入 daemon 日志与 observability。

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
- `skill.json` 可声明 `config_fields`；配置保存到本地 `skill_configs`，支持文本、密钥和带默认值的固定选项，运行脚本或工具时按字段声明显式注入环境变量。`mcp_server_presets` 由 `SkillManager` 渲染为 `stdio` 或 Streamable HTTP server 并按 ID upsert；`skill_python` runtime 复用 Skill 隔离依赖目录，托管认证只持久化配置引用，access/refresh token 留在进程内存并在请求前解析。
- 标准 Agent Skill 安装保留上游根目录 `SKILL.md`，由系统生成 `skill.json` 作为本地检索、能力工作台和调试索引
- 模型选择是对话级的下一轮输入参数，不是底层全局运行态；UI 提交时把当前对话的 `selected_model_id` 和完整 `selected_model_profile` 写入 `run_context`，本地 worker、daemon 和子智能体均优先使用该快照创建 provider。运行中切换模型只更新会话下一轮选择，不会影响已启动流程。
- OpenAI 兼容模型在模型级保存 `api_protocol=chat_completions|responses`；旧配置缺少字段时保持 Chat Completions，新建 GPT‑5.6 默认 Responses。Responses provider 将消息、函数调用和函数结果转换为 typed Items，把 Worker 提供的会话级 key 直接写入顶层 `prompt_cache_key`，并把流式正文、reasoning summary、函数参数、用量和错误重新投影为现有统一事件协议；Chat Completions 继续仅按原有 `prompt_cache_key_param` 配置注入缓存字段；GPT‑5.6 可配置 `none/low/medium/high/xhigh/max` 推理强度。
- Composer 使用 `ProductPopover` 作为主窗口内 overlay：`+` 动作、指定能力和模型选择都在同一 Qt 窗口中锚定、约束边界并处理外部点击，不创建顶层 `Qt.Popup`。浮层通过鼠标全局坐标命中自身与锚点，不能依赖事件接收对象一定是 `QWidget`，以兼容 Windows 原生事件分发。指定能力以会话态 `selected_skill_names` 为唯一数据源。
- 新会话首次发送在完成全部提交预检后、插入用户消息前，同步把空状态从布局移除、隐藏并 `deleteLater()`；后续布局重排和 `processEvents` 不得再次绘制它。普通发送和模型提问只使用会话内交互卡，旧模态入口显式报错而不创建 `QDialog`。Windows daemon/网关进程统一通过 `core.process_utils` 设置 `CREATE_NO_WINDOW` 与隐藏启动信息；首次提交记录 submit/start/run/finish/error 及 committed/rejected，延迟检查任何新增可见顶层窗口并记录具体窗口类名、对象名和标题。
- 会话级 `ui_timeline_v1` 事件账本继续按原顺序保存 Thinking、工具、正文片段和运行中引导，并记录 `group_id`、`stage_id` 和 `reply_kind` 以便实时恢复。所有历史消息都由投影层直接根据标准 OpenAI-compatible 顺序 `assistant.tool_calls → tool → assistant` 重建统一轮次：每个阶段保留独立的思考开关，`AssistantTurnGroup` 统一管理短细分隔线、空阶段可见性与最终操作栏；展开区没有推理或工具时不绘制时间线，也不保留布局高度。携带 `tool_calls` 的 assistant 正文属于阶段回复且不显示消息动作，终止工具循环且正文非空的 assistant 属于最终回复并独占操作栏；引导关闭当前容器并开启新的容器。停止、错误或最终正文为空时显示明确状态并关闭消息动作，不能把前一阶段正文提升为最终答复。工具开始与结果仍通过同一 `tool_call_id` 原位更新，UI 投影字段在进入 Worker 前剥离，原始角色与工具消息结构保持不变。
- 生成办公稿使用本次请求级 `workflow_mode = office_html_first` 和 `office_output_profile` 注入提示；从 HTML 继续生成 PPTX、DOCX、PDF 使用 `workflow_mode = office_file_conversion` 和 `office_conversion_target` 标记；两者都不新增 `RUN_MODE`，因此不改变工具权限。
- PPT Agent 是 `office_html_first + office_output_profile = ppt` 上的一层产品工作流；`core/ppt_agent.py` 注册默认 PPT Agent、Guizang PPT Skill、Frontend Slides、Huashu Design 等 html-ppt 策略，并根据显式选择、偏好、模板和关键词自动选择策略。后三者作为真实 `ai_skills` 包内置，PPT Agent 提交时会把选中的策略映射为临时 `selected_skill_names`，让上游 `SKILL.md`、引用和资源说明进入本轮系统上下文与任务观测页，但不会写回用户会话的手动技能选择。`core/agent.py` 通过 `ppt_agent_mode`、`ppt_agent_strategy`、`ppt_agent_selected_strategy`、`ppt_agent_preference` 和 `ppt_agent_template_file` 注入 PPT Agent 运行提示，但仍要求最终输出 HTML deliverable。
- UI 通过用户消息 `meta.workflow_mode` 将该轮用户消息、工具调用和助手结果渲染成可展开的办公任务卡；消息本体仍按原结构持久化，交付物文件卡渲染在折叠过程外。任务卡会合并最终回复、隐藏气泡识别到的文件卡路径和本轮工具变更中的有效工作区文件，避免最终回复未写完整路径时结果区为空；消息渲染会把当前工作区内的裸完整路径和 Markdown 文件链接统一重写为 `cowork-file:`，再交给右侧文件预览链路处理。任务刚提交时会写入用户请求、附件、选中 Skill 和启动阶段节点；收到系统提示词、Skill 上下文、thinking、工具调用和工具结果后继续追加到过程区，避免展开过程只显示占位。若 live 提交后短时间内过程区仍为空，UI 会补充“等待模型运行接管”的可见状态；daemon 流与普通对话保持一致，不设置单独的模型响应超时，只在 worker 完成、显式失败、用户停止或客户端断开时结束。PPT Agent/办公任务提交链路会写入 `ppt_agent_debug.log`，daemon 流会写入 `daemon.log`，用于定位卡在 UI 提交、任务卡创建、run context 构建、daemon 分发还是后台 worker 启动阶段。历史消息按 render span 独立渲染，避免普通消息和办公任务混排时丢失折叠状态。
- 运行中引导使用用户消息 `meta.same_turn_guidance` 标记，通过 steer 注入当前 in-flight turn；UI 立即结束当前 AI 轮次容器，并在真实发送位置创建独立 guidance 行，普通状态为“等待下一安全节点”，存在未完成工具时为“完成当前步骤后应用”。运行时 `guidance` observability 事件按消息 ID 将原行更新为“已应用”，后续 reasoning 投影到新的轮次容器。`ui_timeline_v1` 只保存在会话展示元数据中，消息上的 `ui_*` 投影字段由 `_messages_for_worker` 剥离，不改变模型协议；新格式损坏时必须显示恢复警告并记录诊断日志。对话投影组件使用横向自适应、纵向内容约束的 QSizePolicy，内部布局统一顶部对齐，只有聊天布局末尾 stretch 可以吸收剩余高度；内容几何变化通过既有滚动节流器合并，且仅在会话仍处于自动跟随状态时滚动到底部。
- `request_user_input` 与审批请求由 `InlineInteractionCard` 渲染在发起会话内，并通过原有 resolver/daemon response 通道返回；resolver 成功后卡片从布局移除，失败时恢复可操作状态并保留输入。日志不记录问题正文或用户回答。
- 内联可视化由默认关闭的 `ai_skills/visualize` 提供。启用时，Python Runner 才向进程注入会话级 `COWORK_VISUALIZATION_DIR`；发布器要求 UTF-8、2 MB 以内、带根 `id` 且无完整文档标签的 HTML Fragment。校验器按资源上下文提取 URL，忽略不会发起网络请求的 SVG/XML 命名空间，仅允许内置 HTTPS CDN 白名单，并把实际 origins 随内容哈希登记到 SQLite；AI 不能通过参数扩大白名单。消息层只识别独占行 `::cowork-inline-vis{file="..."}`，并仅加载会话中已登记且哈希一致的文件；Fragment 位于禁导航、仅允许脚本的 sandbox iframe 中，按产物 origins 生成最小 CSP，继续禁止 fetch/XHR/WebSocket，且只在存在已验证 origins 时开启 WebEngine 远程资源访问。脚本、样式、图片加载失败和 CSP violation 通过 QWebChannel 显示并记录诊断；展示状态仍限制在 64 KB。所有直接创建的 Agent 气泡统一通过会话感知工厂注入 workspace、session、storage 和插件状态；合法指令缺少上下文时显示错误并记录诊断事件，不能静默退化为协议文本。插件关闭时不注册工具、不创建目录、不初始化 WebEngine，历史产物按需只读加载。
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
- MCP servers：通过 `stdio` 或 Streamable HTTP 接入的外部工具；Skill 托管的 server 带有 `source_skill` 归属，保存对应 Skill 配置时自动生成、更新并启用，父 Skill 启停和工具作用域同步继承该归属。Airflow stdio 在测试、发现或调用时按需拉起；官方 Superset MCP 仍由 Superset 侧独立部署，Cowork 只负责认证、连接和工具暴露
- 常驻执行工具：例如 `run_python_code`

设计原则：

- 工具负责执行
- 技能负责经验
- 技能按需披露，默认只给最小摘要
- 技能运行配置由工作台表单维护，缺少必填配置时直接失败，不走静默降级
- 用户可以通过“沉淀为 Skill”把会话经验转为可复用能力。入口先选择当前问答、最近三轮、全部或自定义片段；第一阶段按完整用户任务链组织消息、遮蔽密钥和本地路径，并输出带消息 ID 引用的复用证据。用户在 Linear 风格的分析页确认创建新 Skill、合并增强已有 Skill 或仅追加经验，并显式选择资源候选；第二阶段只接收证据和非敏感目标快照，编译最终草稿。分析与草稿状态持久化到应用数据目录，只有用户主动确认并通过静态校验后才进入能力中心数据源。

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

- **会话**：`core/chat_storage.py` 负责本地消息历史、归档、置顶和项目归属；项目活动排序只使用真实会话 `updated_at`，空项目使用稳定创建时间，浏览和展开项目不更新时间。历史读取失败保持错误占位。
- **模型配置**：显式 `model_channels=[]` 是合法持久化状态，只有缺少该字段的旧配置才迁移默认渠道；空配置使用空 `selected_model_id`，提交预检负责引导用户重新配置。
- **配置**：`core/config_manager.py` 统一管理模型、MCP、Skill 运行配置、工作区、智能体和 UI 偏好；项目归档保存在项目元数据中，左侧栏和项目选择器默认过滤已归档项目，设置中心负责恢复入口
- **记忆**：`core/memory_store.py` 与 `core/memory_update.py` 管理灵魂提示词、全局摘要和工作区摘要
- **技能**：文件系统中的 `SKILL.md`、`skill.json`、`impl.py`、`experience/entries.jsonl`；自动工具发现披露的技能全文是本轮运行时上下文，历史迁移和响应合并会过滤这些隐藏 system 消息，避免把临时技能材料带入后续请求的 prompt cache 前缀
- **自动化**：提示词任务、引用能力、Agent 绑定、计划任务和执行历史保存在本地配置数据中；旧 SOP 模板配置会在迁移时清空，旧模板任务会停用保留并提示用户补充提示词
- **主题持久化**：代码内的 Linear 默认主题是只读基线，永不序列化。应用数据 `themes/` 下每个非下划线 JSON 是一个用户主题，唯一格式为 `format / id / name / overrides`，不含版本、迁移或兼容字段；`_state.json` 为空或只含当前自定义主题 ID。`_preview.json` 保存 `preview_id + revision`、来源会话和覆盖，预览不自动过期，启动时总是丢弃
- **语义注册表**：`core/theme.py` 与 `core/theme_service.py` 将可配置令牌按 global、left_sidebar、conversation、composer、right_sidebar、management、feedback、controls、preview_shell 分组，覆盖颜色、字体缩放、字重、图标、边框、圆角、阴影、间距、滚动条与受限几何；区域令牌未显式覆盖时从当前全局令牌继承
- **动态应用**：`ThemeRuntimeManager` 统一更新 `DesignTokens`、应用字体、QSS、Palette 和聊天 Markdown CSS；`ThemeBindingRegistry` 以弱引用绑定样式、图标、几何和自绘回调，监听 Qt 对象销毁并在刷新前验证底层 C++ 对象仍然有效，既刷新现存控件，也让后创建控件立即取得当前主题。绑定失败会回滚令牌、字体、Palette 和控件，并在 `theme_debug.log` 记录失败控件、刷新数量和各区域耗时；主题已持久化但当前界面刷新失败时，明确提示用户重启应用载入新主题。设置页确有外观变化或 AI 保存并启用主题时，即使即时应用成功也建议重启，确保全部界面完整载入；其他设置保存不触发该提示
- **边界**：主题不能写任意 QSS、隐藏或重排功能、移动组件、替换资源或改变交互。PDF、Office、HTML、图片和可视化只覆盖 Cowork 工具栏、标签、状态与预览外壳；文件内容模板、固定安全恢复条、系统标题栏和原生文件选择器在静态审计白名单中
- **设置与 AI 预览**：设置页只维护内存草稿，选择或编辑不会应用；显式“预览”才校验并一次性刷新，统一保存才原子写入。`skills/theme-customizer` 的 `preview_ui_theme` 创建预览，`patch_ui_theme_preview` 按当前 revision 增量修改；审批与保存必须绑定具体 revision，修改后必须重新确认

## 8. 运行环境

- 桌面 UI 基于 PySide6
- 模型接入支持 OpenAI-compatible 与 Anthropic
- 打包模式保留 Python 基础运行时和 Git Bash
- Node.js、文档读取和金融数据等 Python/Node 依赖改为设置中的可选组件；浏览器自动化独立使用应用管理的 Tencent BrowserSkill `bsk` CLI 与用户确认安装的 Chrome/Edge 扩展，不再依赖 Playwright/UIAutomation
- BrowserSkill CLI 固定版本与 SHA-256，采用临时目录下载、路径安全解压、候选验证和原子目录替换；`browser_skill_cli` 仅接受参数数组并统一处理 JSON、超时、取消、截图路径和敏感 `evaluate` 拒绝，组件缺失或扩展未通过 `doctor` 时明确失败
- BrowserSkill 子进程从启动时把 stdout/stderr 重定向到独立临时文件，避免 Windows 管道写满或 daemon 继承管道句柄造成假超时；连接检查在 `doctor` 后用临时会话执行 Agent Window 范围的 `tab list`，区分“扩展已连接”和“执行通道可用”。组件更新、修复和卸载前由应用停止 daemon，避免运行中的 `bsk.exe` 阻塞原子替换
- 打包版内置 MCP client 运行时，便于直接接入 MCP tools

## 9. 设计取舍

- **优先产品化桌面体验**：把工具链、观察和交付物预览做进 UI，而不是只暴露底层能力。
- **优先单一执行面**：不让 skill、automation、memory 各自演化成新的执行协议。
- **优先显式边界**：工作区、模式、人工确认和技能披露都尽量显式可见。
- **优先可演进性**：用户技能、MCP 工具、提示词自动化和长期记忆都可以持续扩展。
- **会话显示标题**：自动标题继续由首条用户消息生成；用户在侧栏就地重命名后，conversation `meta.manual_title` 标记标题来源，后续异步保存、历史迁移和重新加载必须保留该标题，且不修改数据库表结构。
- **任务观测展示**：`ProductSegmentedControl` 承载执行上下文、调用记录和技术详情；调用记录是工具选择源，技术详情使用共享代码与结果查看器按 Python、Shell、JSON、stdout、stderr 和 Traceback 呈现。
- **UI 导航诊断**：侧栏新建聊天、项目点击、输入栏项目切换和主内容路由向 `ui_navigation.log` 写入 begin/done 阶段；正式 UI 进程通过 `faulthandler` 把原生崩溃线程栈写入 `native_crash.log`。

## 10. 交互表面与 Skill 草稿状态

- `DesignTokens.selection_bg/selection_text` 是全局文字选区语义色；文本控件通过共享菜单保持剪贴板操作一致。
- 每个 `SessionState` 独立关联持久化的 Skill capture ID，阶段统一为分析中、分析待确认、编译中、草稿待确认、失败和已保存。应用重启会把未完成的后台阶段标记为可重试失败，不会假装仍在运行。滚动拖动期间禁止自动滚底与气泡虚拟化重排，确认流程也不读取其他会话的全局草稿值。
- 两个后台阶段采用“先持久化 capture → 创建来源会话任务行 → 启动 worker → 关闭确认弹窗”的交接顺序；启动失败会恢复弹窗和选择，不把关闭弹窗误报为任务已开始。任务行和侧栏 Skill 指示器使用独立、可响应主题变化的状态组件，不修改聊天运行状态或输入可用性。
- 阶段完成通知按 `capture_id + phase` 去重。仅当完成时 `current_session_id` 与 capture 的来源会话不同，才显示右下角 Toast；同会话完成以及开始、进度、失败、恢复只更新来源任务行和侧栏状态，不创建聊天消息或延迟通知队列。
- Skill 编译保存前执行确定性静态质量门：校验 schema、来源引用、敏感字面值、本地硬编码路径、资源路径、Python AST、顶层副作用和目标修订哈希。低置信与缺少验证属于可确认警告；全局错误阻止保存，资源级错误会取消并禁用对应资源。
- `run_python_code` 的原始 `code` 参数不会进入证据模型或第二阶段输入；证据只保留资源用途和消息引用。用户勾选脚本候选后，编译器必须重新生成参数化实现，再经过同一静态质量门。
- AI 正文控件关闭自身滚动条并按完整文档高度撑开，不设置 20,000px 截断；聊天区使用单一外层滚动条，滑块加宽并保留按压期间暂停自动滚底的状态机。
- 技术详情通过 `parse_tool_arguments()` 显式解析字典、JSON 或安全的 Python 字面量。解析失败属于可见错误，不进入静默文本降级。
- `ProductCodeViewer` 负责语言标签、行号、轻量语法高亮和复制；代码参数与其余 JSON 参数使用独立查看器。
- Skill 向导、草稿生成、预览和保存路径写入分阶段诊断日志；Python 异常保留 traceback，原生退出继续由 `native_crash.log` 捕获。

## 11. 代码入口

- `main.py`
- `core/agent.py`
- `core/daemon.py`
- `core/skill_manager.py`
- `core/mcp_client.py`
- `core/automation_manager.py`
- `core/chat_storage.py`
- `core/config_manager.py`

如果需要产品视角的说明，见 [PRODUCT_DOC.md](PRODUCT_DOC.md)；如果需要快速上手，见 [README_CN.md](README_CN.md)；如果需要 5.0.3 发布范围和验收清单，见 [RELEASE_NOTES_5.0.3.md](RELEASE_NOTES_5.0.3.md)。
