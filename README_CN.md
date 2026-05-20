# DeepSeek Cowork

[中文文档](README_CN.md) | [English](README.md)

**DeepSeek Cowork** 是基于 **DeepSeek-V3.2 交错思维链 (Interleaved CoT)** 的 Windows 桌面智能代理框架。它将推理与工具调用融合在一个连续流程中，面向文件、应用与工作流提供稳定可控的自动化能力。（**本项目不是DeepSeek官方开发，纯个人探索和爱好**）

项目团队：**deepseek-cowork team**。

当前应用版本：**4.7.8**。

![应用截图1](images/首页.png)
![应用截图2](images/使用界面.png)

## 🚀 核心特性

### 🧠 推理与工具调用融合
*   **交错式 CoT**：思考中调用工具、观察结果、继续推理，减少幻觉。
*   **工具先探测**：先读取真实文件与环境，再做执行决策。

### 🔌 技能系统
*   **经验优先的技能**：技能被视为结构化经验包，而不是第二套执行协议。
*   **热重载技能**：将新技能放入 `skills/` 或 `ai_skills/`，无需重启即可使用。
*   **可迁移能力包**：Skill 支持导出为 ZIP，也支持从 ZIP 文件或源码文件夹回导。
*   **结构化经验沉淀**：运行中的 lessons learned 可以先写入结构化 entry，再同步回 `SKILL.md` 摘要。
*   **会话沉淀为 Skill**：点击 `沉淀为 Skill` 可将当前会话提炼为可审阅草稿，用于新建 Skill 或更新已有 Skill。
*   **显式只读并行工具**：`parallel_tools` 可并发执行彼此独立的只读工具调用，保持结果顺序，并拒绝写入、审批、命令执行等非只读操作。

### 🖥️ 桌面体验
*   **PySide6 UI**：气泡对话、Markdown 渲染与工具调用卡片。
*   **右侧上下文抽屉**：文件、SOP、任务观测、子 Agent 状态通过紧凑图标按钮按需展开，默认保持主对话区轻量。
*   **会话级控制**：输入区可添加文件、插入智能体、绑定 SOP、指定本会话可用能力或开启反问模式。
*   **多分身监控**：在当前任务内查看并行子任务状态。
*   **手动反馈入口**：侧边栏提供 `更新长期记忆` 与 `沉淀为 Skill`，在人确认后再保存可复用知识。

### 🛰️ 守护进程与 IM 网关
*   **无头守护进程**：后台推理保证 UI 轻量响应。
*   **企业 IM (飞书 / 钉钉 / 企业微信智能机器人)**：通过企业消息下发任务并按日归档。
*   **上下文溢出恢复**：IM 会话遇到上下文长度错误时，可压缩上下文并自动重试一次。
*   **工作区约束**：未开启 God Mode 时，IM 指令同样受工作区限制。

## 📦 安装指南

### 选项 1：运行可执行文件 (Windows)
1.  前往 [Releases](../../releases) 下载最新版。
2.  解压并运行 `deepseek-cowork.exe`。
3.  无需安装 Python。

### 选项 2：源码运行 (Windows)
**前置要求**：Python 3.10+

1.  克隆仓库：
    ```bash
    git clone https://github.com/chuancyzhang/deepseek-cowork.git
    cd deepseek-cowork
    ```

2.  创建并使用虚拟环境：
    ```bash
    python -m pip install -r requirements.txt
    ```

3.  启动应用：
    ```bash
    python main.py
    ```

### 打包前运行时准备（Windows）
在执行 `pyinstaller deepseek-cowork.spec` 之前，先拉取固定版本的运行时包（Node.js + Git Bash），并进行 SHA256 校验：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\fetch_runtimes.ps1
```

下载文件会放在 `.runtime_downloads/`，解压后目录为 `node_env/` 与 `git_bash_env/`。

## 📖 使用指南

### 1. 配置
打开 **⚙️ 设置**：
*   **API Key**：DeepSeek 或其他模型提供商密钥。
*   **Provider**：`openai`（兼容 DeepSeek）或 `anthropic`。
*   **God Mode**：安全沙箱开关。

### 2. 选择工作区
选择你的工作目录，Agent 只能在该范围内读写文件。

### 3. 开始自动化
示例：
*   *“扫描这个项目中未使用的 import 并移除。”*
*   *“汇总文件夹里的所有 PDF 并生成报告。”*
*   *“创建一个使用 yt-dlp 的视频下载技能。”*

### 4. 闭合反馈回路
在完成有价值的会话后，可以使用侧边栏入口：
*   **`更新长期记忆`**：扫描新增或变更的历史会话，分批合并写入 `memories.md`；支持进度展示、缩小到后台、结果预览、手动编辑与再次保存。
*   **`沉淀为 Skill`**：将当前会话生成 Skill 草稿，可新建 Skill，也可对已有 Skill 追加经验或重写说明；保存前可预览和编辑。
*   **功能中心导入/导出**：可从文件夹或 ZIP 导入自定义能力，也可将现有 Skill 导出为可迁移 ZIP 包。

### 5. 企业消息
打开 **⚙️ 设置 → 企业消息**，在飞书、钉钉或企业微信智能机器人分组中填写对应凭据，启用渠道并启动网关。

### 6. 应用更新
打开 **⚙️ 设置 → 更新** 检查 GitHub Releases。打包版会下载、校验、解压暂存并通过独立更新器重启安装；源码运行模式只检查版本并提供 Releases 页面入口。

## 🏗️ 架构概览

*   **`main.py`**：PySide6 桌面 UI 入口。
*   **`core/agent.py`**：推理循环与工具调度。
*   **`core/daemon.py`**：无头推理服务。
*   **`core/im_gateway/`**：多平台企业消息网关与渠道适配。
*   **`core/skill_manager.py`**：工具注册、经验包加载、相关性匹配与 Prompt 注入。
*   **`core/sop_manager.py`**：会话级 SOP 模板、步骤状态、确认、重跑与跳过逻辑。
*   **`core/updater.py`**：GitHub Releases 检查、安装包校验、暂存和 Windows 重启更新器。
*   **`skills/`**：内置系统技能。
*   **`ai_skills/`**：AI 或用户创建的技能。

## 🧩 Tool + Experience 模型
- `tool` 是模型唯一直接调用的执行面。
- 工具源于技能目录中的公开 Python 函数（`impl.py`），自动生成 JSON Schema 并被模型调用。
- `skill` 是结构化经验包，承载边界、坑点、经验、推荐流程与推荐工具。
- 新经验默认可先写入结构化 entry，再回写到 `SKILL.md` 摘要。
- `沉淀为 Skill` 是人工确认的沉淀入口，会把会话经验转化为 `SKILL.md`、`skill.json`、`experience/entries.jsonl` 与可选 `impl.py`。
- Skill 可以导出为 ZIP 包；ZIP 导入会校验安全路径，支持平铺根目录或单文件夹根目录，优先沿用元数据中的原 Skill 名称，并拒绝同名覆盖。
- 新技能仍然支持热加载，无需重启。
- 当前源码已打通“提示模型记录经验 -> 调用 `update_experience` -> 持久化经验 -> 后续任务再次注入”的链路，因此 AI 在合适场景下可以主动调用经验工具。
- 但这仍属于可用的第一版经验闭环：系统会鼓励并支持 AI 记录经验，不保证每次都会主动调用，也尚未内建经验验证、效果评估、生命周期治理等强反馈机制。

## 🔄 Agentic Workflow
- 交错式 CoT：思考 → 工具调用 → 观察 → 继续思考 → 最终回答。
- 流式事件：思考/内容/工具调用/工具结果实时回传 UI。
- 环路保护：检测重复思考或重复工具签名并自动停止。
- 支持暂停/恢复/停止，安全管控长任务。
- 会话 SOP 会把执行约束在当前步骤内，直到用户在抽屉中确认完成、重跑或标记不适用。
- 反问模式仅开放只读探索，并通过交互工具收集关键问题答案后再回到正常执行。

## 🧠 分层记忆与上下文
- 系统上下文：工作区、操作系统、Python、日期与操作规则。
- 记忆层：可选 `memories.md` 自动注入，承载稳定偏好与长期信息；在架构上，记忆被视为经验系统中的长期层，而不是独立于经验系统之外的另一套协议。
- 长期记忆通过 `更新长期记忆` 手动触发，系统用 `memories_update_state.json` 记录已处理历史，后续更新聚焦新增或变更会话。
- 技能提示：先注入最小经验摘要，需要时再展开完整说明。
- 会话级指定能力与智能体配置会收窄当前任务的可用能力范围。
- 渐进式披露：references、结构化经验 entry 和更大的目录上下文只在必要时展开。
- 历史清洁：每轮清理/折叠思考内容，避免上下文污染。

## 🛠️ 扩展开发
在 `skills/` 新建文件夹：
1.  `SKILL.md`：经验包正文。
2.  `skill.json`：能力分组、tool refs、workflow、披露默认值等元数据。
3.  可选 `impl.py`：当技能需要新增可调用工具时使用。
4.  可选 `experience/entries.jsonl`：结构化运行经验。

推荐心智模型：
- `tool` = 执行器
- `skill` = 经验包

完整说明见 [SKILL_SYSTEM.md](SKILL_SYSTEM.md)。

## 📄 许可证

MIT License
