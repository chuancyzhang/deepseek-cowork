# DeepSeek Cowork

[中文文档](README_CN.md) | [English](README.md)

**DeepSeek Cowork** is a Windows desktop agent framework built on **DeepSeek-V3.2 Interleaved Chain-of-Thought**. It combines reasoning with tool use to plan, execute, and refine tasks across files, apps, and workflows in a secure desktop environment.

Built by **deepseek-cowork team**.

![intro](images/english_intro.png)
![App Screenshot 1](images/首页.png)
![App Screenshot 2](images/使用界面.png)

## 🚀 Key Features

### 🧠 Reasoning with Tool Use
*   **Interleaved CoT**: The agent thinks, calls tools, observes results, and continues reasoning in a single flow.
*   **Tool-First Exploration**: Reads real files before acting, reducing hallucinations.

### 🔌 Skill System
*   **Hot-Reloadable Skills**: Drop new skills into `skills/` or `ai_skills/` and use them immediately.
*   **Self-Evolving Skills**: Success/failure signals update `SKILL.md` experience for better future runs.

### 🖥️ Desktop Experience
*   **PySide6 UI**: Modern chat bubbles, markdown rendering, and tool-call cards.
*   **Workspace Sidebar**: File tree and previews without leaving the app.
*   **Sub-Agent Monitor**: Observe parallel workers and their statuses.

### 🛰️ Daemon & IM Gateway
*   **Headless Daemon**: Background inference keeps UI responsive.
*   **Enterprise IM (Feishu)**: Send commands via IM with daily session rotation.
*   **Workspace Guardrails**: IM requests follow the same workspace limits unless God Mode is enabled.

## 📦 Installation

### Option 1: Run from Executable (Windows)
1.  Download the latest release from [Releases](../../releases).
2.  Unzip and run `deepseek-cowork.exe`.
3.  No Python installation required.

### Option 2: Run from Source (Windows)
**Prerequisites**: Python 3.10+

1.  Clone the repository:
    ```bash
    git clone https://github.com/chuancyzhang/deepseek-cowork.git
    cd deepseek-cowork
    ```

2.  Create and use the virtual environment:
    ```bash
    pip install -r requirements.txt
    ```

3.  Run the application:
    ```bash
    python main.py
    ```

## 📖 Usage Guide

### 1. Configuration
Open **⚙️ Settings**:
*   **API Key**: DeepSeek or other provider key.
*   **Provider**: `openai` (DeepSeek compatible) or `anthropic`.
*   **God Mode**: Toggle safety sandbox restrictions.

### 2. Select Workspace
Pick your working directory. The agent treats it as the boundary for file access.

### 3. Start Automating
Examples:
*   *"Scan this project for unused imports and remove them."*
*   *"Summarize all PDFs in this folder into a single report."*
*   *"Create a new skill to download videos using yt-dlp."*

### 4. Enterprise IM (Feishu)
Open **⚙️ Settings → Enterprise Messaging**, fill in **Feishu App ID / App Secret**, then start the gateway.

## 🏗️ Architecture

*   **`main.py`**: PySide6 desktop UI entry.
*   **`core/agent.py`**: Reasoning loop and tool orchestration.
*   **`core/daemon.py`**: Headless inference server.
*   **`core/im_gateway.py`**: Feishu IM integration.
*   **`core/skill_manager.py`**: Skill loading and prompt injection.
*   **`skills/`**: Built-in system skills.
*   **`ai_skills/`**: AI or user-created skills.

## 🛠️ Extending
Create a folder in `skills/` with:
1.  `impl.py`: Python implementations.
2.  `SKILL.md`: Usage guidance and metadata.

## 📄 License

[MIT License](LICENSE)
