# DeepSeek Cowork

[中文文档](README_CN.md) | [English](README.md)

**DeepSeek Cowork** 是一款受到 **DeepSeek-V3.2 交错思维链 (Interleaved Chain-of-Thought)**  启发而打造的新一代桌面智能代理框架。它利用最新的**链式思考 (CoT) 与工具调用**能力，能够规划、执行并在复杂的任务流中不断自我进化。

通过实现**交错式思维链 (Interleaved CoT)** 架构，该 Agent 不仅仅是“聊天”——它能像人类工程师一样，在安全且现代化的桌面环境中，主动思考、编写代码、执行系统指令并分析结果，形成持续的工作闭环。

![应用截图1](images/首页.png)
![应用截图2](images/使用界面.png)

## 🚀 核心特性

### 🧠 双引擎支持
*   **DeepSeek R1/V3**: 全面支持 DeepSeek 最新的推理模型，利用其强大的思维链能力解决复杂问题。
*   **Moonshot AI (Kimi 2.5)**: 原生集成 Kimi API，针对长文本理解和严格的工具调用格式进行了深度优化。

### ⚡ 交错式 CoT 与 "God Mode"
*   **思考-调用-思考 (Think-Call-Think)**: Agent 将推理过程 (`<think>`) 与实际工具执行交织在一起。它会规划步骤，运行工具（如搜索网页、运行 Python 脚本），读取输出，然后根据结果修正计划。
*   **God Mode (上帝模式)**: 专为极客用户设计。开启后可绕过安全沙箱，赋予 Agent 对系统级子进程、注册表和文件系统的完全访问权限，实现无限制的自动化操作。

### 🔌 自进化技能系统
*   **热重载技能**: 只需将新的 Python 脚本放入 `skills/` 或 `ai_skills/` 目录，Agent 无需重启即可立即识别并使用新能力。
*   **AI 生成技能**: Agent 可以编写自己的工具（例如 `yt-dlp` 封装器或特定的数据爬虫）并保存以供未来使用。
*   **经验学习**: 系统会跟踪工具使用的成功与失败案例，自动更新 `SKILL.md` 文档，"教会" Agent 下次如何更准确地使用工具。

### 🖥️ 现代化桌面体验
*   **原生 UI**: 基于 PySide6 构建，采用清爽的 **16:9** 宽屏设计，强制 **Light Mode (浅色模式)** 以保持专业一致的视觉体验。
*   **实时反馈**: 通过可折叠的 "Thinking" 气泡，实时观察思考过程与工具调用。
*   **工作区沙箱**: 默认情况下，所有操作仅限于您选择的项目文件夹内，确保系统安全。

### 🛰️ 守护进程模式
*   **后台推理**: 在无头守护进程中执行推理，让 UI 保持轻量。
*   **流式事件**: 守护进程运行时实时回传思考与工具调用。

## 📦 安装指南

### 选项 1: 运行可执行文件 (Windows)
1.  前往 [Releases](../../releases) 页面下载最新的发布版本。
2.  解压并运行 `deepseek-cowork.exe`。
3.  无需安装 Python 环境。

### 选项 2: 源码运行
**前置要求**: Python 3.10+

1.  克隆仓库:
    ```bash
    git clone https://github.com/chuancyzhang/deepseek-cowork.git
    cd deepseek-cowork
    ```

2.  安装依赖:
    ```bash
    pip install -r requirements.txt
    ```

3.  启动应用:
    ```bash
    python main.py
    ```

## 📖 使用指南

### 1. 配置
启动应用并点击右上角的 **⚙️ 设置** 图标:
*   **API Key**: 输入您的 DeepSeek 或其他大模型提供商密钥。
*   **Provider**: 在 `openai` (适用于 DeepSeek) 或 `anthropic` 之间选择。
*   **God Mode**: 根据需要开启或关闭安全限制。

### 2. 选择工作区
点击文件夹图标选择您的工作目录。Agent 将把这个文件夹视为它的“世界”，并在此范围内自由读写文件。

### 3. 开始自动化
使用自然语言输入您的需求。例如:
*   *"扫描此项目中的未使用的 import 并将它们移除。"*
*   *"搜索科技巨头的最新股价并绘制趋势图。"*
*   *"创建一个新技能，使用 yt-dlp 下载 YouTube 视频。"*

### 4. 可视化监控
*   **Chat Tab**: 主交互界面。
*   **Sub-Agent Monitor**: 当主 Agent 派发子任务并行处理时，可在此实时查看子 Agent 的日志和状态。

## 🏗️ 架构概览

*   **`core/`**: 核心大脑。
    *   `agent.py`: 处理主事件循环、线程管理和 CoT 逻辑。
    *   `llm/`: OpenAI 和 Moonshot API 的适配层。
    *   `skill_manager.py`: 负责动态工具注册和 Prompt 注入。
*   **`skills/`**: 内置系统能力 (文件 I/O, Python 执行器, 网络搜索)。
*   **`ai_skills/`**: 用户或 AI 创建的扩展能力。
*   **`main.py`**: PySide6 前端入口。
*   **`core/daemon.py`**: 负责后台推理的无头守护进程。

## 🛠️ 扩展开发
要添加新能力，只需在 `skills/` 中创建一个文件夹，包含:
1.  `impl.py`: 您的 Python 函数实现。
2.  `SKILL.md`: 描述这些函数在何时以及如何被调用。

系统会自动通过 LLM 将它们连接起来！

## 📄 许可证

[MIT License](LICENSE)
