# DeepSeek Cowork

[中文文档](README_CN.md) | [English](README.md)

**DeepSeek Cowork** 是基于 **DeepSeek-V3.2 交错思维链 (Interleaved CoT)** 的 Windows 桌面智能代理框架。它将推理与工具调用融合在一个连续流程中，面向文件、应用与工作流提供稳定可控的自动化能力。（**本项目不是DeepSeek官方开发，纯个人探索和爱好**）

项目团队：**deepseek-cowork team**。

![应用截图1](images/首页.png)
![应用截图2](images/使用界面.png)

## 🚀 核心特性

### 🧠 推理与工具调用融合
*   **交错式 CoT**：思考中调用工具、观察结果、继续推理，减少幻觉。
*   **工具先探测**：先读取真实文件与环境，再做执行决策。

### 🔌 技能系统
*   **经验优先的技能**：技能被视为结构化经验包，而不是第二套执行协议。
*   **热重载技能**：将新技能放入 `skills/` 或 `ai_skills/`，无需重启即可使用。
*   **结构化经验沉淀**：运行中的 lessons learned 可以先写入结构化 entry，再同步回 `SKILL.md` 摘要。

### 🖥️ 桌面体验
*   **PySide6 UI**：气泡对话、Markdown 渲染与工具调用卡片。
*   **工作区侧边栏**：文件树与内容预览一体化。
*   **多分身监控**：查看并行子任务状态。

### 🛰️ 守护进程与 IM 网关
*   **无头守护进程**：后台推理保证 UI 轻量响应。
*   **企业 IM (飞书)**：通过飞书下发任务并按日归档。
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

### 4. 企业消息 (飞书)
打开 **⚙️ 设置 → 企业消息**，填写 **飞书 App ID / App Secret** 并启动网关。

## 🏗️ 架构概览

*   **`main.py`**：PySide6 桌面 UI 入口。
*   **`core/agent.py`**：推理循环与工具调度。
*   **`core/daemon.py`**：无头推理服务。
*   **`core/im_gateway.py`**：飞书 IM 集成。
*   **`core/skill_manager.py`**：工具注册、经验包加载、相关性匹配与 Prompt 注入。
*   **`skills/`**：内置系统技能。
*   **`ai_skills/`**：AI 或用户创建的技能。

## 🧩 Tool + Experience 模型
- `tool` 是模型唯一直接调用的执行面。
- 工具源于技能目录中的公开 Python 函数（`impl.py`），自动生成 JSON Schema 并被模型调用。
- `skill` 是结构化经验包，承载边界、坑点、经验、推荐流程与推荐工具。
- 新经验默认可先写入结构化 entry，再回写到 `SKILL.md` 摘要。
- 新技能仍然支持热加载，无需重启。

## 🔄 Agentic Workflow
- 交错式 CoT：思考 → 工具调用 → 观察 → 继续思考 → 最终回答。
- 流式事件：思考/内容/工具调用/工具结果实时回传 UI。
- 环路保护：检测重复思考或重复工具签名并自动停止。
- 支持暂停/恢复/停止，安全管控长任务。

## 🧠 分层记忆与上下文
- 系统上下文：工作区、操作系统、Python、日期与操作规则。
- 记忆层：可选 `memories.md` 自动注入，承载稳定偏好与长期信息。
- 技能提示：先注入最小经验摘要，需要时再展开完整说明。
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

[MIT License](LICENSE)
