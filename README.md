# DeepSeek Cowork

[中文文档](README_CN.md) | [English](README.md)

DeepSeek Cowork is a powerful desktop application powered by the **DeepSeek-V3.2 Interleaved Chain-of-Thought (CoT)** engine. It leverages the latest **Thinking with Tool Use** capability to automatically plan and execute complex file operations through natural language.

Unlike traditional chatbots, this assistant utilizes the **DeepSeek-V3.2** model, which can invoke tools directly within its thinking process (`<think>`). Through a "Think-Call-Think" interleaved flow, it precisely plans tasks, explores the environment, and safely executes actions—whether it's batch file processing, data analysis, or complex agentic workflows.

![intro](images/english_intro.png)

![App Screenshot](images/首页.png)


## 🚀 Key Features

*   **🧠 Powered by DeepSeek-V3.2**: 
    *   **Interleaved CoT**: The industry's first model to support tool calling within thinking mode. The agent not only plans but also actively explores its environment (e.g., listing files, reading content) during the `<think>` process, adjusting strategies in real-time based on feedback.
    *   **SOTA Inference**: Based on DeepSeek-V3.2 (performance comparable to GPT-5), balancing reasoning capability with response speed, optimized specifically for Agent scenarios.
*   **🔌 Modular Skills Center**: 
    *   **Visual Management**: Built-in "Skills Center" panel to visually manage installed skills.
    *   **Categorized Display**: Automatically separates "Standard Skills" and "AI Generated Skills" for better organization.
    *   **Dynamic Evolution**: The agent can solidify general algorithmic logic into new skills, continuously evolving.
*   **🛡️ Secure Execution**:
    *   **Workspace Sandbox**: Operations are strictly confined to the user-selected directory.
    *   **AST Static Analysis**: Static code analysis prevents unauthorized path access before execution.
    *   **Security Policy**: The agent is explicitly instructed to only create new skills for algorithmic/system operations, avoiding misuse.
*   **🤖 Multi-Agent Dispatch**: Capable of spawning sub-agents (`agent-manager`) to handle parallel tasks independently.
*   **💾 Auto-Save History**: Chat sessions are automatically saved and restored, allowing seamless continuation of tasks.
*   **⏯️ Real-time Control**: Supports pausing/resuming tasks at any time, and forcibly stopping execution if stuck in a loop.
*   **🖥️ Modern UI**: Built with **PySide6** (Qt for Python), offering a responsive and native desktop experience.

## 📦 Installation

### Option 1: Run from Executable (Windows)
1.  Download and unzip `dist/deepseek-cowork.zip`.
2.  Run `deepseek-cowork/deepseek-cowork.exe`.
3.  No Python installation required.

### Option 2: Run from Source
**Prerequisites**: Python 3.10+

1.  Clone the repository:
    ```bash
    git clone https://github.com/chuancyzhang/deepseek-cowork.git
    cd deepseek-cowork
    ```

2.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```

3.  Run the application:
    ```bash
    python main.py
    ```

## 📖 Usage Guide

1.  **Configuration**:
    *   Launch the app and click the **⚙️ Settings** button.
    *   Enter your **DeepSeek API Key**.
    *   Check and manage enabled skills in the "Skills Center".

2.  **Select Workspace**:
    *   Click "Select Workspace" to choose the folder you want the agent to work on. **The agent has NO access to files outside this folder.**

3.  **Start Chatting**:
    *   Enter a command, e.g.:
        *   *"Search for the latest news on DeepSeek and summarize the V3.2 features"*
        *   *"Convert all .docx files in this folder to PDF"*
        *   *"Read sales.xlsx and generate a sales trend chart"*
    *   Watch the **Thinking** process in the right panel to experience how V3.2 utilizes interleaved tool calls.

4.  **Control Tasks**:
    *   Use the **⏸️ Pause** and **⏹️ Stop** buttons at the bottom to control the AI execution flow in real-time.

## 🏗️ Architecture

This project fully leverages the new features of DeepSeek-V3.2:

*   **`core/`**:
    *   `agent.py`: Implements the **Interleaved CoT** Agent logic, handling the tool call loop within thinking mode.
    *   `skill_manager.py`: Manages dynamic tool loading, metadata parsing (supports multi-language descriptions), and categorization.
*   **`skills/`**:
    *   Plugins that extend functionality. Each skill has its own `impl.py` and `SKILL.md`.
*   **`main.py`**: PySide6 GUI entry point, containing components like the Skills Center (Tabbed view) and Task Monitor (Thinking process display).

## 🛠️ Developing New Skills

To add a new skill:
1.  Create a folder in `skills/` (e.g., `skills/git-helper`).
2.  Add `SKILL.md` to define the skill's purpose and prompts (adding `description_cn` is recommended).
3.  Add `impl.py` with Python functions. The `SkillManager` will automatically detect and register these functions as tools for the LLM.

## 📄 License

[MIT License](LICENSE)
