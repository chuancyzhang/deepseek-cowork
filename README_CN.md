# 智能文件助手 (DeepSeek 版)

智能文件助手是一款强大的桌面应用程序，利用 **DeepSeek 先进的推理模型 (R1/V3)**，通过自然语言自动执行复杂的文件操作。

与传统的聊天机器人不同，本助手采用 **思维链 (Chain-of-Thought, CoT)** 方法来规划、生成并安全地执行 Python 代码以完成您的请求——无论是批量重命名文件、分析数据，还是重组项目目录。

![应用截图](placeholder-screenshot.png)

## 🚀 核心功能

*   **🧠 DeepSeek 推理核心**: 采用“推理-编码”模式。智能体在采取行动前会先通过 `<think>` 进行思考，确保处理复杂任务时具有更高的准确性。
*   **🔌 模块化技能系统**: 基于类 MCP 架构构建。功能被封装为模块化的“技能 (Skills)”（例如 `file-system` 文件系统, `agent-manager` 智能体管理），易于扩展。
*   **🛡️ 安全执行**:
    *   **工作区沙箱**: 操作严格限制在用户选择的目录中。
    *   **AST 静态分析**: 在执行前通过静态代码分析防止未授权的路径访问（例如 `../`，绝对路径）。
*   **🤖 多智能体调度**: 能够生成子智能体 (`agent-manager`) 来独立处理并行任务。
*   **💾 历史记录自动保存**: 聊天会话会自动保存和恢复，支持无缝继续之前的任务。
*   **✨ 动态技能学习**: 智能体可以将成功的代码解决方案转化为可复用的技能，不断扩展自身能力。
*   **🛡️ 增强安全性**: 关键操作（如删除文件）会触发显式的用户确认弹窗，防止误操作。
*   **🖥️ 现代 UI**: 使用 **PySide6** (Qt for Python) 构建，提供响应迅速的原生桌面体验，并支持折叠推理日志。

## 📦 安装

### 选项 1: 运行可执行文件 (Windows)
1.  进入 `dist` 目录。
2.  运行 `DeepSeekAgent.exe` (单文件可执行程序)。
3.  无需安装 Python。

### 选项 2: 源码运行
**前提条件**: Python 3.10+

1.  克隆仓库:
    ```bash
    git clone https://github.com/chuancyzhang/smart-file-assistant.git
    cd smart-file-assistant
    ```

2.  安装依赖:
    ```bash
    pip install PySide6 requests openai colorama
    ```

3.  运行应用程序:
    ```bash
    python main.py
    ```

## 📖 使用指南

1.  **配置**:
    *   启动应用并点击 **⚙️ 设置 (Settings)** 按钮。
    *   输入您的 **DeepSeek API Key** (可选填 Base URL)。
    *   根据需要启用/禁用特定技能。

2.  **选择工作区**:
    *   点击 "Select Workspace" 选择您希望智能体操作的文件夹。**智能体无法访问该文件夹之外的文件。**

3.  **开始对话**:
    *   输入指令，例如:
        *   *“把这个文件夹里所有的 .txt 文件重命名为 .md”*
        *   *“读取 data.csv 并告诉我 'Price' 列的平均值”*
        *   *“创建一个名为 'backup' 的新文件夹并将所有图片移动进去”*
    *   观察 **Thinking (思考)** 过程（点击可展开），了解智能体如何规划任务。

## 🏗️ 架构

本项目采用模块化设计:

*   **`core/`**:
    *   `agent.py`: 管理 LLM 交互和对话历史。
    *   `skill_manager.py`: 处理工具的动态加载和 Schema 生成。
    *   `config_manager.py`: 持久化用户设置。
*   **`skills/`**:
    *   扩展功能的插件。每个技能（如 `file-system`）都有自己的 `impl.py` 和 `SKILL.md` 定义。
*   **`main.py`**: PySide6 GUI 入口点。

## 🛠️ 开发新技能

添加新技能的步骤:
1.  在 `skills/` 中创建一个文件夹（例如 `skills/git-helper`）。
2.  添加 `SKILL.md` 定义技能的用途和提示词。
3.  添加 `impl.py` 编写 Python 函数。`SkillManager` 会自动检测并将这些函数注册为 LLM 可用的工具。

## 📄 许可证

[MIT License](LICENSE)
