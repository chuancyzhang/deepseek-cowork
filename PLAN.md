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

## 6. 下一阶段：V3.1 演进计划 - 深度接管与现代体验

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

### 6.6 双模式引擎：规划与执行 (Dual-Mode Engine)
为了应对复杂任务，引入显式的“规划-执行”分离机制。
-   [x] **即时执行模式 (Instant Mode)**：
    -   [x] 当前默认行为。适合简单指令（如“列出文件”、“读取代码”）。
    -   [x] 特点：低延迟，立即行动。
-   [x] **深度规划模式 (Deep Planning Mode)**：
    -   [x] 适合复杂任务（如“重构整个模块”、“从零构建 Web 应用”）。
    -   [x] **Step 1: 提案 (Proposal)**：Agent 分析需求，生成结构化的执行计划（包括步骤、涉及文件、潜在风险）。
    -   [x] **Step 2: 审阅 (Review)**：用户在 UI 上查看计划，可以调整步骤顺序、添加约束或修改目标。
    -   [x] **Step 3: 执行 (Execution)**：获得批准后，Agent 逐步执行计划，并在每个关键节点自动保存检查点 (Checkpoint)，支持随时回滚。

---

> **结语**：
> 每个人都不必成为程序员，但每个人都可以拥有程序员的力量。通过 DeepSeek Cowork V3.1，我们将赋予你真正的“上帝视角”，让代码成为你手中的积木，随心所欲地创造未来。
