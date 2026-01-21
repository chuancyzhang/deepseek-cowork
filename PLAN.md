# DeepSeek Cowork 演进路线图 (Roadmap)

本文档记录 DeepSeek Cowork 的版本演进计划与长期路线图。

## 当前重点：V3.0 演进计划 - 从工具到全能 Agent 平台

> **核心愿景**：打破普通用户与开源世界的壁垒。让 GitHub 上数以万计的优秀开源项目，一键转化为 Cowork 的 **Skills**，让 AI Agent 成为连接人类知识宝库与个人需求的桥梁。

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

### Phase 1: 基础建设 (Current)
- [x] 核心 Skill 架构 (`skills/` 目录)。
- [x] 基础 Skill Creator (`create_new_skill`)。
- [x] 常用办公/搜索 Skill。
- [x] UI 体验优化：
    - [x] 增加 DeepSeek API Key 获取指引 (platform.deepseek.com)。
    - [x] Agent 回复支持 Markdown 渲染。

### Phase 2: 开源连接器 (Next Step)
- [x] **会话转技能 (Session to Skill)**:
    - [x] UI 支持：在代码执行卡片增加“保存为技能”按钮。
    - [x] 逻辑泛化：利用 LLM 将当前会话中的一次性代码重构为通用函数（参数提取、去硬编码）。
    - [x] 自动注册：调用 `skill-creator` 生成持久化文件。
- [ ] 优化 `Skill Creator`，增强对 GitHub 仓库的分析能力（结合 Web Search Skill）。
- [ ] 实现 `yt-dlp` 的完整封装案例，打通“环境依赖自动安装”流程。
- [ ] 实现 Skill 的持久化存储与热加载。

### Phase 3: 平台化与自进化
- [ ] 开发 `Skill Manager` GUI 面板。
- [ ] 实现 Skill 的“经验回写”机制。
- [ ] 推广至更多开源项目 (`FFmpeg`, `Pake` 等)。

---

> **结语**：
> 每个人都不必成为程序员，但每个人都可以拥有程序员的力量。通过 DeepSeek Cowork V3.0，我们将 GitHub 变成普通人的“军火库”，让 AI 替你学习、替你配置、替你封装，你只需要负责——**创造**。
