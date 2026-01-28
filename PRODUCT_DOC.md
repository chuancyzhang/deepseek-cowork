# DeepSeek Cowork - 产品文档

## 1. 产品概述 (Product Overview)

### 1.1 背景
DeepSeek 最近发布了 **DeepSeek-V3.2**，引入了革命性的 **交错思维链 (Interleaved Chain-of-Thought)** 技术，允许模型在思考过程中调用工具。这打破了以往“先思考，再行动”的线性模式，使得 AI 能够在思考中途主动探索环境、验证假设，从而解决更加复杂的任务。

**DeepSeek Cowork** 正是基于这一核心能力构建的下一代桌面智能助手。它将 V3.2 的强大推理能力带入用户的本地工作流，提供前所未有的文件处理与自动化体验。

### 1.2 目标
面向希望提高效率的专业用户和开发者，提供一款基于 **DeepSeek-V3.2 Interleaved CoT** 的跨平台桌面应用。它不仅能理解自然语言，更能在执行任务时像人类专家一样“边做边想”，确保操作的准确性与鲁棒性。

### 1.3 核心价值
*   **DeepSeek-V3.2 驱动**：利用最新的模型技术，推理能力媲美 GPT-5，并在 Agent 任务中表现卓越。
*   **交错思维链**：支持“思考 + 工具调用”的深度融合，解决“难解答，易验证”的复杂问题。
*   **安全可控**：在强大的能力之上，提供沙箱、AST 检查与人工确认三重防护。
*   **可扩展与自进化**：Skills / MCP 功能中心支持动态加载新技能，且 Agent 可通过经验回写机制（Self-Evolution）不断优化技能表现。

---

## 2. 功能特性 (Features)

### 2.1 核心引擎：DeepSeek-V3.2 Interleaved CoT

1.  **思考融入工具调用**
    *   **旧模式 (ReAct)**：Think -> Act -> Observe -> Think... 步骤繁琐，容易迷失。
    *   **旧模式 (CoT)**：一次性想清楚 -> 写代码 -> 执行。如果信息不足（如不知道文件名），容易瞎编。
    *   **新模式 (DeepSeek-V3.2)**：在 `<think>` 标签内部，模型可以发起工具调用（如 `list_files`），获取结果后继续思考。这使得模型在生成最终方案前，已经对环境有了充分的认知。

2.  **高性能与低延迟**
    *   V3.2 相比 Kimi-K2-Thinking，大幅降低了输出长度和计算开销，响应更迅速。
    *   适合日常 Agent 场景，如文件整理、数据分析、网络搜索等。

### 2.2 桌面助手平台化 (Platform)

1.  **对话式文件操作**
    *   自然语言驱动，支持批量重命名、格式转换、数据提取等。
    *   **实时控制**：支持随时暂停/停止任务。

2.  **Skills Center (功能中心)**
    *   **双轨制管理**：清晰区分 `skills`（内置系统技能）和 `ai_skills`（AI/用户生成的技能）。
    *   **动态加载**：支持热加载新的 Python 技能包，无需重启。

3.  **自进化机制 (Self-Evolution)**
    *   **经验回写**：当 Agent 在使用某个技能（如 `yt-dlp-wrapper`）遇到问题并成功解决后（例如发现需要安装 `ffmpeg`），会自动将这一经验写入 `SKILL.md` 的 `experience` 字段。
    *   **知识复用**：下次再调用该技能时，Agent 会自动读取之前的经验，避免重蹈覆辙。

4.  **安全与鲁棒性**
    *   **工作区沙箱**：限制文件访问范围。
    *   **环境一致性**：内置 `env_utils`，确保在打包后的 EXE 环境中也能正确调用 Python 和 `pip` 安装依赖。
    *   **网络增强**：针对 GitHub Clone 等操作内置指数退避重试机制。

5.  **工作区侧边栏 (Workspace Sidebar)**
    *   **可视化文件管理**：在窗口右侧实时展示工作区文件树。
    *   **快速预览**：点击文件即可预览内容（支持文本、代码等），无需离开应用。

---

## 3. 用户使用流程 (User Flow)

1.  **配置与启动**
    *   设置 DeepSeek API Key。
    *   选择工作区目录。

2.  **任务下达**
    *   用户：“帮我整理这个文件夹，把所有去年的财务报表（文件名含 2024 和 report）移动到 'Archive/2024' 目录。”

3.  **交错思维执行 (DeepSeek-V3.2)**
    *   **Think**: 我需要先看下当前有哪些文件。
    *   **Call**: `list_files(".")`
    *   **Observe**: `['2024_report_Q1.xlsx', 'photo.jpg', '2023_data.csv'...]`
    *   **Think**: 我看到了 '2024_report_Q1.xlsx'。我需要创建 'Archive/2024' 目录，然后移动文件。
    *   **Call**: `run_python("os.makedirs(...)")`
    *   **Think**: 任务完成。

4.  **结果反馈**
    *   用户在界面上看到简洁的“已完成整理”，并可在聊天气泡中查看详细的思考与操作记录。
    *   同时，可通过右侧 **工作区侧边栏** 实时查看文件变化，无需切换到资源管理器。

---

## 4. 技术架构 (Technical Architecture)

*   **GUI**: PySide6 (Qt for Python)
*   **LLM**: DeepSeek-V3.2 (API)
*   **Pattern**: Interleaved Chain-of-Thought (Thinking with Tool Use)
*   **Core**:
    *   `Agent`: 负责推理与工具调度。
    *   `SkillManager`: 负责技能的加载 (`skills/` vs `ai_skills/`)、持久化与经验追踪。
    *   `EnvUtils`: 负责跨环境（Dev/Prod）的运行时环境适配。
*   **Backend**: Python, PyInstaller

---

## 5. 模型对比 (Why DeepSeek-V3.2?)

| 特性 | DeepSeek-V3.2 | DeepSeek-V3.1 | ReAct Agents |
| :--- | :--- | :--- | :--- |
| **思考模式** | 支持 (Interleaved) | 支持 (Linear) | 不支持 |
| **工具调用** | **Thinking 中可调用** | 仅最后调用 | 循环调用 |
| **Agent 性能** | **SOTA (GPT-5 Level)** | 强 | 一般 |
| **延迟** | 低 | 中 | 高 |
