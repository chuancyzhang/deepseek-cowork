# DeepSeek Cowork 演进路线图 (Roadmap)

本文档记录 DeepSeek Cowork 的版本演进计划与长期路线图。

## 当前重点：V3.1 演进计划 - 深度接管与现代体验 (God Mode & Modern UI)

> **核心愿景**：从“开发者的工具”进化为“普通人的助手”。打破安全沙箱的束缚，赋予 Agent 真正的系统控制权；告别简陋的调试界面，带来现代化的对话体验。

---

## 1. 核心理念升级：Skill-First (技能优先)

V3.0 将不再局限于“文件自动化工具”，而是升级为 **“可无限扩展的个人 AI 智能体平台”**。

-   **旧模式 (V2.0)**：内置固定功能（Python 脚本、文件操作） + 简单的插件。
-   **新模式 (V3.0)**：**Everything is a Skill**。
    -   核心能力（Web 搜索、Office 处理）是 Skill。
    -   用户自定义脚本是 Skill。
    -   **GitHub 开源项目是 Skill**。

---

## 2. 杀手级功能：GitHub to Skill (开源即技能)

这是 V3.0 的灵魂功能。利用 DeepSeek-V3.2 的代码理解与规划能力，实现“开源项目自动化封装”。

### 2.1 交互流程
1.  **需求提出**：用户输入“我想要下载 B 站视频”或直接贴入 GitHub 链接（如 `yt-dlp`）。
2.  **智能搜索 (Thinking)**：Agent 搜索 GitHub，找到最匹配的高星项目（如 `yt-dlp`, `ffmpeg`, `pake`）。
3.  **自动封装 (Skill-Creator)**：
    -   **Plan 模式**：分析项目结构、依赖、使用文档。
    -   **Code 模式**：编写 Python Wrapper，处理环境依赖（自动安装 pip 包），生成 `SKILL.md` 和 `impl.py`。
4.  **即刻可用**：封装完成后，用户直接对话即可调用该能力。

### 2.2 典型应用场景
-   **多媒体处理**：封装 `FFmpeg` / `ImageMagick` -> “帮我把这个视频转成 GIF，压缩到 5MB 以内”。
-   **视频下载**：封装 `yt-dlp` -> “下载这个 YouTube 播放列表里的所有视频”。
-   **应用生成**：封装 `Pake` -> “把 Notion 网页版打包成一个独立的 .exe 桌面应用”。
-   **万能格式转换**：封装 `Pandoc` / `LibreOffice` -> “把这些 Markdown 文档转成 Word 格式”。
-   **解密与安全**：封装 `Ciphey` -> “帮我看看这段加密且编码过的文本是什么意思”。

---

## 3. 技能生态系统 (Skill Ecosystem)

### 3.1 Skill Creator (技能生成器 - 已初步实现)
-   **功能增强**：
    -   支持从 URL 直接读取代码/文档。
    -   支持多文件 Skill 结构。
    -   **依赖隔离**：为每个复杂 Skill 建立独立的虚拟环境或依赖管理机制，避免冲突。

### 3.2 Skill Manager (技能管理器 - 新增)
-   **可视化面板**：
    -   **已安装技能**：查看、启用/禁用、删除。
    -   **技能详情**：显示技能描述、适用场景、依赖库。
    -   **自进化记录**：查看技能在运行过程中积累的“经验”（Prompt 优化）。
-   **操作命令**：
    -   “卸载视频下载技能”
    -   “更新所有技能的依赖”

### 3.3 自进化机制 (Self-Evolving Skills)
-   **经验回写**：
    -   首次运行 Skill 遇到问题（如 `yt-dlp` 需要 Cookie），解决后，AI 自动将解决方案（如“检查 cookies.txt 路径”）写入 Skill 的 System Prompt 或文档中。
    -   下次运行时，Skill 自动拥有该经验，速度起飞。

---

## 4. GUI 与交互优化

为了支撑上述功能，界面需要配合升级：

1.  **技能商店/仓库页**：
    -   虽然是本地运行，但可以展示推荐的“热门开源项目 Skill 化配方”。
    -   一键“Clone & Skillify”。

2.  **开发模式切换**：
    -   **User 模式**：日常使用，对话即服务。
    -   **Dev 模式**：能够看到 Skill 生成的详细日志、Plan 过程、代码编译输出（类似 `Claude Code` 的终端视图）。

3.  **状态反馈**：
    -   在封装 Skill 时，展示清晰的进度条：`分析项目` -> `规划接口` -> `编写代码` -> `安装依赖` -> `测试运行`。

---

## 5. 路线图 (Roadmap)

### Phase 1: 基础建设 (Stable)
- [x] 核心 Skill 架构 (`skills/` 目录)。
- [x] 基础 Skill Creator (`create_new_skill`)。
- [x] 常用办公/搜索 Skill。
- [x] UI 体验优化：
    - [x] 增加 DeepSeek API Key 获取指引 (platform.deepseek.com)。
    - [x] Agent 回复支持 Markdown 渲染。

### Phase 2: 开源连接器 (Stable)
- [x] **会话转技能 (Session to Skill)**:
    - [x] UI 支持：在代码执行卡片增加“保存为技能”按钮。
    - [x] 逻辑泛化：利用 LLM 将当前会话中的一次性代码重构为通用函数（参数提取、去硬编码）。
    - [x] 自动注册：调用 `skill-creator` 生成持久化文件。
- [x] 优化 `Skill Creator`，增强对 GitHub 仓库的分析能力（结合 Web Search Skill）。
- [x] **GitHub 集成增强**:
    - [x] `clone_repository` 增加重试机制，提升网络不佳时的稳定性。
    - [x] 智能过滤大文件 (.gitignore) 避免 Push 失败。
- [x] **AI 生产技能架构**:
    - [x] 建立 `ai_skills` 目录，隔离 System 技能与 AI 生成技能。
    - [x] 示例：将 `yt-dlp-wrapper` 迁移为标准 AI 技能。

### Phase 3: 平台化与自进化 (Current V3.0)
- [x] **Skill Manager (核心升级)**:
    - [x] 实现 `SkillManager` 对 `skills` (内置) 和 `ai_skills` (用户/AI) 的统一加载与管理。
    - [x] 实现 Skill 的持久化存储与热加载。
- [x] **Self-Evolving Skills (自进化)**:
    - [x] 引入 Meta-Tools (`meta-tools` skill)。
    - [x] 实现 `update_skill_experience`：Agent 可自动将运行经验回写到 `SKILL.md`，实现自我迭代。
- [x] **环境鲁棒性**:
    - [x] 实现 `env_utils.py`，确保在 IDE 开发模式和 Exe 打包模式下均能正确调用 Python 环境与 pip。
- [ ] 推广至更多开源项目 (如 FFmpeg, Pake 等) -> 验证“开源即技能”闭环。

---

## 6. 里程碑：V3.2 (Current Released) - 深度接管与现代体验

### 6.1 解除封印：God Mode (上帝模式)
- [x] **核心机制实现**:
    - [x] ConfigManager 添加 `god_mode` 状态管理。
    - [x] File System Skill: 绕过沙箱路径检查 (Path Traversal Check)。
    - [x] Python Runner Skill: 绕过 AST 静态安全分析。
    - [x] UI 设置：添加“启用 God Mode”开关与红色警示。
- [x] **增强功能**:
    - [x] 注册表编辑与系统服务管理支持 (Python Runner God Mode 支持 `winreg`/`subprocess`)。
    - [x] 风险操作二次确认机制优化 (弹窗内容可复制，SystemToast 警告)。
-   **全系统文件访问**：不再局限于 `workspace` 目录，允许访问全盘文件 (C:/, D:/ 等)。
-   **系统级操作**：
    -   允许修改注册表、系统环境变量。
    -   允许管理系统服务、进程 (Task Kill)。
-   **极致透明度 (Radical Transparency)**：
    -   **操作解构**：在执行任何代码前，必须用**自然语言**清晰解释代码意图（例如：“我将为您修改注册表以添加右键菜单，这将涉及以下键值...”）。
    -   **代码翻译**：对于复杂的 Shell/Python 脚本，提供简要的“人类可读版”摘要，确保用户知道每一行代码在做什么，杜绝“黑盒操作”。
    -   **风险预警**：在执行高危操作（如删除文件、杀进程）前，明确提示潜在风险并保留用户的“最终确认权”。
-   **应用接管**：
    -   能够启动任意本地应用程序。
    -   未来探索：通过 GUI Automation (PyAutoGUI/UIA) 点击其他软件的按钮。

### 6.2 现代对话体验 (Modern UI Overhaul)
告别简陋的“开发者调试界面”，打造真正的消费级桌面助手体验。
- [x] **气泡式对话 (Chat Bubbles)**：
    - [x] **User**: 右侧对齐，现代气泡风格，支持文本复制。
    - [x] **Agent**: 左侧对齐，GitHub 风格 Markdown 渲染。
    - [x] **System/Error**: 升级为 SystemToast 通知，支持内容复制。
- [x] **富文本渲染**:
    - [x] Markdown 完美支持（表格、列表、代码块）。
    - [x] 代码块语法高亮与“一键复制”按钮。
- [x] **动态交互**:
    - [x] **Thinking 过程可视化**：DeepSeek 风格折叠/展开组件，包含思考耗时显示。
    - [x] **工具调用卡片**：将 `list_files`, `run_command` 等工具调用渲染为精美的卡片，而非原始 JSON。
    - [x] **运行记录融合**：将运行记录（Running Log）与思考过程（Thinking）合并，移除独立面板，简化界面。
    - [x] **工作区侧边栏 (Workspace Sidebar)**：
        - [x] 在窗口最右侧增加工作区文件树与内容预览面板。
        - [x] 方便用户无需离开应用即可查看项目结构和文件内容。
    - [x] **多分身协作可视化 (Multi-Agent Visualization)**：
        - [x] **Sub-Agent Monitor**: 实时监控子智能体的思考过程 (Thinking) 与工具调用 (Tool Use)。
        - [x] **状态感知**: 动态展示分身状态（Pending/Thinking/Action/Completed）与耗时。

### 6.3 数据持久化与无缝迁移 (Data Persistence)
解决“每次更新都要重新配置”的痛点，实现程序与数据的分离。
-   [x] **用户数据分离 (User Data Separation)**：
    -   [x] 将所有可变数据（对话历史、安装的 Skill、配置文件、API Key）存储在标准的用户目录（如 Windows 的 `%APPDATA%\DeepSeekCowork` 或用户指定的 `data` 目录）。
    -   [x] 程序本体（exe/zip）可以随意删除、解压覆盖，而不会丢失任何历史记录。
-   [x] **便携模式支持 (Portable Mode)**：
    -   [x] 如果在程序同级目录下检测到 `user_data` 文件夹，则优先读取该目录（适合 U 盘携带）。
-   [x] **自动迁移助手 (Migration Assistant)**：
    -   [x] 当检测到旧版本数据结构不兼容时，自动运行迁移脚本，确保平滑升级。

### 6.4 极致轻量化 (Software Diet)
随着功能增加，软件体积日益膨胀。V3.1 将致力于打造“小而美”的核心运行时。
-   [x] **按需加载依赖 (Lazy Loading)**：
    -   [x] 不再预装所有可能的库（如 `yt-dlp`, `pandas`）。
    -   [x] 初始包仅包含核心 Agent 逻辑与基础环境。
    -   [x] 当用户需要特定技能时，Agent 自动检测并动态下载/安装所需依赖。
-   **运行时瘦身**：
    -   **Python 环境裁剪**：使用高度定制的 Embedded Python，剔除 `tkinter`, `unittest`, `pydoc` 等无用标准库。
    -   **UPX 压缩**：对编译后的二进制文件与 DLL 进行高压缩比处理。
-   **架构升级探索**：
    -   探索从 PySide6 (Qt) 迁移至更轻量的 **PyWebView** 或 **Tauri (Backend only)** 架构，利用系统自带 Webview 渲染 UI，可减少 ~100MB 体积。

### 6.5 浏览器深度集成 (Deep Browser Integration)
不再是孤立的桌面程序，而是与您的默认浏览器（Chrome/Edge）无缝连接。
-   [x] **上下文感知 (Context Awareness)**：
    -   [x] 能够读取当前浏览器打开的标签页 URL 和标题。
    -   [x] 场景：“总结我当前正在看的这篇论文” —— 无需手动复制粘贴链接。
-   [x] **自动化操作 (Browser Automation)**：
    -   [x] 通过 Playwright 或浏览器调试协议 (CDP)，控制浏览器执行重复性任务。
    -   [x] 场景：“帮我把这 50 个网页都保存成 PDF”、“自动登录并填写今天的日报”。
-   **Cookie/Session 桥接**：
    -   (需用户授权) 安全复用浏览器的登录状态。
    -   让 Skill（如 `yt-dlp` 或爬虫）直接继承您的会员权限，无需繁琐的手动导出 Cookie。

---
    
## 7. 下一阶段：V3.3 演进计划 - 记忆与历史 (Memory & History)

### 7.1 历史记录增强 (Enhanced History)
- [x] **自定义存储路径**: 支持用户配置聊天记录的存放目录。
- [x] **全量还原**: 点击历史记录时，不仅还原对话内容，还能完整还原推理过程 (Reasoning Content) 和工具调用状态。
- [x] **思考过程修复**: 加载历史消息时确保思考过程不丢失并可回放。

### 7.2 长期记忆与存储重构 (Long-term Memory & Storage Refactoring)
**目标**: 采用 "SQLite (日志) + Markdown (记忆)" 的混合存储架构，兼顾高性能检索与人类可读性。

#### 7.2.1 数据库架构设计 (Database Architecture)
- [x] **SQLite Schema (日志层)**:
    - **`conversations`**: 会话容器
        - `id` (PK, UUID): 会话唯一标识
        - `title`: 会话标题 (支持自动生成)
        - `created_at` / `updated_at`: 时间戳
        - `status`: 会话状态 (active, archived, deleted)
        - `meta`: JSON 字段，存储 context (如 IDE 环境信息, git branch)
    - **`messages`**: 消息实体
        - `id` (PK, UUID)
        - `conversation_id` (FK)
        - `role`: (user/assistant/system/tool)
        - `content`: 消息正文
        - `tool_calls`: JSON 存储工具调用详情
        - `reasoning_content`: 存储思维链 (CoT) 数据
        - `token_count`: 预估 Token 数 (用于上下文窗口计算)

#### 7.2.2 检索与增强
- [x] **多层检索策略 (Multi-Tier Retrieval Strategy)**:
    - **L1 语义记忆 (`memories.md`)**:
        - 每次会话启动时，自动加载 `memories.md` 内容至 System Context，提供全局记忆支持。
    - **L2 历史回溯 (SQLite FTS5)**:
        - 创建 `messages_fts` 虚拟表，对 `content` 和 `reasoning_content` 建立倒排索引，支持毫秒级全文搜索。
        - **上下文注入**: User 发送消息 -> 提取关键词 -> FTS 检索 Top-K 相关历史片段 -> 注入 System Prompt 的 `Relevant History` 区块。
        - **排序优化**: 结合 Time Decay (时间衰减) 算法，优先展示近期相关的历史记录。
    - **L3 向量检索 (SQLite-vec)**:
        - 引入 `sqlite-vec` 插件，为 `messages` 表添加向量字段 (Embedding)，支持语义相似度搜索。
        - **混合检索**: 结合 FTS5 (关键词匹配) 和 Vector Search (语义匹配) 提升召回准确率，弥补关键词匹配在语义理解上的不足。

#### 7.2.3 迁移与兼容 (Migration & Compatibility)
- [x] **存量数据迁移**: 开发 `migrate_files_to_sqlite.py` 脚本，遍历 `history/` 目录下的 JSON/MD 文件并导入数据库。

#### 7.2.4 语义记忆与自动提炼 (Semantic Memory & Auto-Refinement)
- [x] **混合存储架构 (Hybrid Storage)**:
    - **`memories.md`**: 采用 Markdown 格式存储高层语义记忆（用户偏好、项目规则、核心知识），与 SQLite 数据库同级存放。
    - **优势**: 保持人类可读性，便于用户手动微调；同时作为 System Prompt 的一部分直接注入 LLM 上下文。
- [x] **记忆更新机制 (Memory Update Mechanism)**:
    - **后台任务**: 会话结束 (Session End) 或闲置 (Idle) 时触发后台任务。
    - **核心提取**: AI 读取当前 `memories.md` 和最近会话记录，智能判断是**追加**新知识还是**修正**旧记忆，避免重复，形成自我进化的记忆闭环。

#### 7.2.5 主动检索技能 (Active Retrieval Skill)
- [x] **History Query Skill**:
    - **场景触发**: 当用户提及特定时间（"上周的讨论"）或话题（"之前关于数据库的方案"）时，AI 识别意图并调用工具。
    - **能力**: 支持按 `keywords` (关键词) 和 `date_range` (时间范围) 组合过滤 SQLite 中的 `messages`。
    - **交互**: 将检索到的关键历史片段作为 Tool Output 返回给 Agent，用于回答用户问题。

### 7.3 多模型兼容 (Multi-Model Support)
- [x] **Minimax**: 接入 Minimax API (兼容 Anthropic 协议) [Docs](https://platform.minimaxi.com/docs/api-reference/text-anthropic-api)。
- [x] **GLM-4.7**: 接入智谱 AI GLM-4 API [Docs](https://open.bigmodel.cn/dev/api/normal-model/glm-4)。
- [x] **Kimi 2.5**: 接入 Moonshot AI Kimi k2.5 API [Docs](https://platform.moonshot.ai/docs/guide/kimi-k2-5-quickstart)。
- [x] **千问max**: 接入千问max API [Docs](https://modelstudio.console.alibabacloud.com/ap-southeast-1/?tab=doc#/doc/?type=model&url=2840914_2&modelId=qwen3-max。
- [x] **International Models**: 增加对主流国际模型的支持，支持自定义model名 (Gemini, GPT, Claude)。
- [x] **Multi-modal**: 支持多模态读写 (图片/视频输入支持)。

### 7.4 体验与工作区增强 (UX & Workspace)
- [x] **API Key 保存反馈**: 密钥输入后提示保存成功（如 SystemToast）。
- [x] **工作区多类型预览**: 支持图片等常见格式的预览展示。
- [x] **系统级文件操作**: 工作区文件支持复用操作系统的打开、复制、移动、删除等操作。
- [x] **默认工作区路径**: 支持设置默认工作区，启动时自动加载。
- [x] **多会话并行**: 同时开启并管理多个对话会话，互不干扰。
- [x] **设置保存提示优化**: 点击设置保存后提示信息不再显示。
- [x] **主题自动跟随系统**: 使用 pyqtdarktheme 统一样式并自动切换深色/浅色。

### 7.5 常驻运行与企业消息接入 (Always-On & Enterprise Messaging)
**目标**: 实现 "Headless Daemon" 架构，使 Agent 能在后台静默运行，并通过企业 IM 随时响应指令。

#### 7.5.1 守护进程与资源管理 (Daemon & Resource Mgmt)
- [ ] **C/S 架构分离**:
    - **Daemon (Server)**: 无头后台进程，负责 LLM 推理、工具执行和消息路由。
    - **GUI (Client)**: 轻量级前端，仅负责渲染对话和配置，关闭窗口不影响后台运行。
    - **System Tray**: 托盘图标支持“显示/隐藏”、“退出”、“查看状态”。
- [ ] **智能休眠 (Smart Suspend)**:
    - **Context Swapping**: 闲置超过 N 分钟后，将显存/内存中的 LLM 上下文序列化到磁盘，释放资源。
    - **Wake-on-Request**: 收到新消息（API/IM）时自动热加载上下文恢复运行。

#### 7.5.2 统一消息网关 (Unified Messaging Gateway)
- [ ] **Provider Abstraction**:
    - 定义 `IMProvider` 接口 (`send_message`, `parse_event`, `verify_signature`)。
    - 内置轻量级 Webhook Server (FastAPI) 用于接收回调。
- [ ] **多平台适配**:
    - **飞书 (Feishu)**: 适配飞书开放平台 Event Callback V2，支持卡片消息 (Interactive Cards) 渲染工具调用结果。
    - **企业微信 (WeCom)**: 适配接收消息 API，支持 XML 解密与被动响应。
    - **钉钉 (DingTalk)**: 适配钉钉机器人 Webhook 与 Stream 模式（无需公网 IP）。
- [ ] **会话映射 (Session Mapping)**:
    - 建立 `IM_User_ID` <-> `Cowork_Conversation_ID` 的持久化映射表，确保在 IM 中的多轮对话上下文连续。

### 7.6 技能即时可用 (Instant Skill Availability)
- [x] **自动发现与热加载**: AI 创建技能后自动扫描并注册，无需手动刷新。
- [x] **对话侧即时可用**: 新技能在当前会话中立刻可被调用。

### 7.7 系统工具增强 (System Tools Enhancement)
- [x] **Grep 升级 (Everything 集成)**:
    - **全盘检索**: 集成 Everything (ES) 命令行工具 (`es.exe`)，实现对整个操作系统的文件和文件夹进行毫秒级即时检索。
    - **突破限制**: 不再局限于当前工作区 (Workspace)，赋予 Agent 真正的全盘视野。
    - **智能回退**: 若未检测到 Everything 服务，可以提醒用户是否安装Everything，然后自动降级为原有 Grep 模式。

### 7.8 Gemini 3 深度集成与全能多模态 (Gemini 3 & Omni-Multimodal)
基于 [Gemini 3 文档](https://ai.google.dev/gemini-api/docs/gemini-3?hl=zh-cn)，打造全能多模态体验。

- [ ] **Gemini 3 模型接入**:
    - **Core Models**: 接入 `gemini-3-pro-preview` (强推理) 和 `gemini-3-flash-preview` (高性价比)。
    - **SDK 升级**: 迁移/兼容新的 `google.genai` Python SDK。
- [ ] **视觉理解 (Visual Understanding)**:
    - **Image Reading**: 利用 Gemini 3 的多模态能力，支持对上传图片的深度分析、OCR 和物体识别。
    - **Video Reading**: 支持上传视频文件，通过 API (Video context) 读取视频内容并进行语义检索或总结。
- [ ] **视觉生成 (Visual Generation)**:
    - **Image Generation**: 接入 `gemini-3-pro-image-preview` (Nano Banana)，支持对话生成高质量图像。
    - **Video Generation**: 探索 Veo 或相关 Video API，支持根据文本描述生成视频片段。
- [ ] **多模态交互体验 (Multimodal UI)**:
    - **Media Rendering**: 聊天气泡支持原生渲染 API 返回的 Image/Video 对象，而非仅显示链接。
    - **Preview**: 在输入框支持粘贴/拖拽图片和视频进行预览与上传。

### 7.9 定时任务与自动化 (Scheduled Tasks & Automation)
- [ ] **任务调度引擎 (Scheduler Engine)**:
    - 集成 `APScheduler`，支持 Cron 表达式、间隔执行 (Interval) 和一次性延时任务 (Date)。
    - **持久化**: 任务配置存储于 SQLite，确保重启后自动恢复。
- [ ] **定时技能 (Scheduled Skills)**:
    - **Schedule Skill**: 允许 Agent 调用工具注册定时任务（例如：“每天早上 9 点帮我总结昨天的 Hacker News”）。
    - **回调机制**: 任务触发时，自动唤醒 Agent 并注入特定的 Prompt context 执行后续操作。
- [ ] **任务管理面板**:
    - 提供 GUI 界面查看当前挂起的定时任务、下次执行时间及历史执行日志。

### 7.10 应用管理与深度操控 (App Management & Deep Control)
- [ ] **智能应用索引 (App Indexing)**:
    - **自动扫描**: 启动时自动扫描开始菜单快捷方式、注册表卸载列表和 PATH 环境变量，构建 `App Name -> Exe Path` 映射表。
    - **Everything 集成**: 利用 Everything 接口实现对未收录应用的模糊查找（如 "Find where Photoshop is installed"）。
- [ ] **应用启动技能 (App Launcher Skill)**:
    - **`launch_app(name, args)`**: 支持自然语言模糊匹配启动应用（如 "打开微信", "启动 VS Code 并打开当前项目"）。
    - **`open_with(file, app)`**: 指定特定应用打开文件（如 "用 Chrome 打开这个 HTML"）。
- [ ] **GUI 自动化与智能操控 (GUI Automation)**:
    - **Browser-Use 集成**: 引入 `browser-use` 或 Playwright，赋予 Agent 操控浏览器的能力（点击、输入、滚动），实现网页任务自动化（如“帮我订一张去北京的票”）。
    - **OS-Level Control**: 探索 `pywinauto` / `UIAutomation`，实现对非 Web 原生应用（如微信、飞书、系统设置）的点击与操作。
    - **视觉反馈循环**: 结合 Gemini 3 的视觉能力，截屏 -> 分析 UI 布局 -> 规划点击坐标 -> 执行操作。

---

> **结语**：
> 每个人都不必成为程序员，但每个人都可以拥有程序员的力量。通过 DeepSeek Cowork V3.2，我们将赋予你真正的“上帝视角”，让代码成为你手中的积木，随心所欲地创造未来。
