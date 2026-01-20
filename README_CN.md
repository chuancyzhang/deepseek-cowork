# DeepSeek Cowork

DeepSeek Cowork 是一款基于 **DeepSeek-V3.2 交错思维链 (Interleaved Chain-of-Thought)** 的强大桌面应用程序。它利用最新的 **思考融入工具调用 (Thinking with Tool Use)** 能力，通过自然语言自动规划并执行复杂的文件操作。

与传统的聊天机器人不同，本助手采用 **DeepSeek-V3.2** 模型，能够在思考模式下同时调用工具，通过“思考-调用-再思考”的交错过程，精准地规划任务、生成代码并安全地执行，无论是批量处理文件、数据分析，还是复杂的智能体任务。

![应用截图](placeholder-screenshot.png)

## 🚀 核心功能

*   **🧠 DeepSeek-V3.2 驱动**: 
    *   **交错思维链**: 业内首个支持在思考模式下进行工具调用的模型。智能体在 `<think>` 过程中不仅能规划，还能主动探索环境（如列出文件、读取内容），根据反馈实时调整策略，大幅提升任务准确率。
    *   **极致推理**: 基于 DeepSeek-V3.2（性能媲美 GPT-5），平衡了推理能力与响应速度，专为 Agent 场景优化。
*   **🔌 模块化技能中心**: 
    *   **可视化管理**: 内置“功能中心”面板，直观展示已安装的技能。
    *   **分类展示**: 自动区分“标准功能模块”和“AI 生成的技能”。
    *   **动态进化**: 智能体可以将通用的算法逻辑固化为新技能，不断进化。
*   **🛡️ 安全执行**:
    *   **工作区沙箱**: 操作严格限制在用户选择的目录中。
    *   **AST 静态分析**: 在执行前通过静态代码分析防止未授权的路径访问。
    *   **安全策略**: 智能体被明确指示仅为算法/系统操作创建新技能，避免滥用。
*   **🤖 多智能体调度**: 能够生成子智能体 (`agent-manager`) 来独立处理并行任务。
*   **💾 历史记录自动保存**: 聊天会话会自动保存和恢复，支持无缝继续之前的任务。
*   **⏯️ 实时控制**: 支持随时暂停/继续任务，以及在陷入死循环时强制停止。
*   **🖥️ 现代 UI**: 使用 **PySide6** (Qt for Python) 构建，提供响应迅速的原生桌面体验。

## 📦 安装

### 选项 1: 运行可执行文件 (Windows)
1.  下载并解压 `dist/deepseek-cowork.zip`。
2.  运行 `deepseek-cowork/deepseek-cowork.exe`。
3.  无需安装 Python。

### 选项 2: 源码运行
**前提条件**: Python 3.10+

1.  克隆仓库:
    ```bash
    git clone https://github.com/chuancyzhang/deepseek-cowork.git
    cd deepseek-cowork
    ```

2.  安装依赖:
    ```bash
    pip install -r requirements.txt
    ```

3.  运行应用程序:
    ```bash
    python main.py
    ```

## 📖 使用指南

1.  **配置**:
    *   启动应用并点击 **⚙️ 设置 (Settings)** 按钮。
    *   输入您的 **DeepSeek API Key**。
    *   在“功能中心”查看和管理已启用的技能。

2.  **选择工作区**:
    *   点击 "Select Workspace" 选择您希望智能体操作的文件夹。**智能体无法访问该文件夹之外的文件。**

3.  **开始对话**:
    *   输入指令，例如:
        *   *“搜索一下 DeepSeek 最新发布的新闻，并总结 V3.2 的特性”*
        *   *“把这个文件夹里所有的 .docx 文件转成 PDF”*
        *   *“读取 sales.xlsx 并生成一份销售趋势图表”*
    *   观察右侧面板的 **深度思考** 过程，体验 V3.2 模型如何在思考中交错调用工具。

4.  **控制任务**:
    *   使用底部的 **⏸️ 暂停** 和 **⏹️ 停止** 按钮来实时控制 AI 的执行流程。

## 🏗️ 架构

本项目充分利用了 DeepSeek-V3.2 的新特性：

*   **`core/`**:
    *   `agent.py`: 实现了支持 **交错思维链** 的 Agent 逻辑，处理思考模式下的工具调用循环。
    *   `skill_manager.py`: 管理工具的动态加载、元数据解析（支持中文描述）和分类管理。
*   **`skills/`**:
    *   扩展功能的插件。每个技能都有自己的 `impl.py` 和 `SKILL.md`。
*   **`main.py`**: PySide6 GUI 入口点，包含 Skills Center（Tab 分页）、Task Monitor（思考过程展示）等组件。

## 🛠️ 开发新技能

添加新技能的步骤:
1.  在 `skills/` 中创建一个文件夹（例如 `skills/git-helper`）。
2.  添加 `SKILL.md` 定义技能的用途和提示词（推荐添加 `description_cn` 字段）。
3.  添加 `impl.py` 编写 Python 函数。`SkillManager` 会自动检测并将这些函数注册为 LLM 可用的工具。

## 📄 许可证

[MIT License](LICENSE)
