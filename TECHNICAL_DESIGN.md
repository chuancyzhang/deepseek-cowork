# Agent 通用事件处理逻辑设计 (Technical Design)

## 1. 核心理念：DeepSeek Reasoning Mode (Thinking -> Coding)

我们放弃传统的 **ReAct (Reason + Act)** 循环模式，转而采用 **DeepSeek R1** 风格的 **推理-编码 (Reasoning-Coding)** 模式。

### 1.1 模式对比
*   **旧模式 (ReAct)**:
    *   Think: 我需要看下有哪些文件。
    *   Action: `list_files()`
    *   Observation: `['data.csv']`
    *   Think: 我需要读取它。
    *   Action: `read_file('data.csv')`
    *   ... (多轮交互，速度慢，易出错)
*   **新模式 (DeepSeek CoT)**:
    *   **Input**: 用户指令 + 当前目录结构快照。
    *   **Reasoning (`<think>`)**: 用户想要转换 CSV。我看到目录下有 `data.csv`。我应该使用 pandas 读取它，清洗数据，然后保存为 Excel。需要注意处理编码问题...
    *   **Code Generation**: 一次性生成包含所有步骤的完整 Python 脚本。
    *   **Execution**: 本地环境运行该脚本。
    *   **Result**: 任务完成。

### 1.2 优势
*   **减少延迟**: 只有一轮 LLM 调用。
*   **逻辑连贯**: 复杂的逻辑在 Thinking 阶段被完整规划，代码质量更高。
*   **透明度**: 将 `<think>` 内容展示给用户，增强信任感。

---

## 2. 架构组件 (Architecture Components)

### 2.1 Agent Core (DeepSeek Powered)
*   **Prompt 策略**: 
    *   System Prompt 中不再定义复杂的 Tool Schema。
    *   注入当前 Context（工作区文件列表、环境库版本）。
    *   使用 DeepSeek API 的 `reasoning_content` 特性。
    *   **Tool Calling**: 支持原生工具调用（如 `list_files`），允许模型在生成代码前探索环境。
*   **Parser**:
    *   直接读取 API 返回的 `reasoning_content` 字段 -> UI "思考中..." 面板。
    *   支持多轮工具调用循环 (Reasoning -> Tool Call -> Tool Output -> Reasoning -> Final Code)。
    *   提取 `content` 中的 `python` 代码块 -> 安全检查器 -> 执行器。

### 2.2 UI Layer Update
*   **Chat Interface**: 增加 "思维链折叠/展开" 功能，展示 Agent 的心路历程 (Reasoning Content)。
*   **Status**: 从 "Running Tool..." 变为 "DeepSeek Thinking..." -> "Executing Code...".


### 2.3 Executor (执行器)
*   **职责**: 解析 Agent 的指令，安全地运行工具，并将结果标准化返回。
*   **特性**: 错误处理、超时控制、权限验证。
*   **Interaction Bridge**:
    *   **Input Interception**: 劫持 Python `input()` 函数，将其转换为 UI 层的弹窗请求 (`__REQUEST_INPUT__` 信号)。
    *   **GUI Modal**: 支持 Yes/No 确认框和文本输入框，实现后台代码与前台用户的同步交互。

### 2.4 Skill Manager (技能管理器)
*   **动态加载**: 支持从 `skills/` (内置) 和 `user_skills/` (用户自定义) 双路径加载技能。
*   **自动沉淀**: 将生成的有效代码自动封装为新技能，无需重启即可立即生效。

---

## 3. 数据流示例 (Data Flow Example)

**用户输入**: "把当前目录下所有的 PNG 图片重命名为 JPG。"

**Round 1:**
*   **Context**: 用户指令
*   **Agent Thought**: 用户想处理文件。我需要先知道当前目录下有哪些文件。
*   **Agent Action**: Call `list_files({ path: "." })`
*   **Executor**: 执行 `ls`，返回 `['a.png', 'b.png', 'doc.txt']`

**Round 2:**
*   **Context**: 历史 + `list_files` 结果
*   **Agent Thought**: 我看到了两个 PNG 文件。我需要编写一个脚本来重命名它们。
*   **Agent Action**: Call `generate_and_run_script({ language: "python", code: "..." })`
*   **Executor**: 运行 Python 脚本，返回 "Success"

**Round 3:**
*   **Context**: 历史 + 脚本执行成功
*   **Agent Thought**: 任务已完成。
*   **Agent Final Answer**: "已成功将 a.png 和 b.png 转换为 JPG 格式。"

---

## 4. 核心接口定义 (Core Interfaces)

```typescript
interface AgentContext {
  history: Message[];
  variables: Record<string, any>;
}

interface Tool {
  name: string;
  description: string;
  execute: (params: any) => Promise<string>;
}

interface AgentResult {
  type: 'action' | 'answer';
  content: any;
}
```

## 4. 技术架构 (Python Desktop App)

### 4.1 核心选型
采用纯 Python 技术栈开发，利用 Python 丰富的生态系统，简化开发与部署流程。

*   **GUI 框架**: **PySide6 (Qt for Python)**
    *   **理由**: 工业级 UI 框架，支持现代化界面，组件丰富，跨平台表现一致。
*   **打包工具**: **PyInstaller** 或 **Nuitka**
    *   **理由**: 将 Python 解释器、依赖库和脚本打包为独立的 `.exe` (Windows) 或 `.app` (Mac)，用户无需安装 Python。

### 4.2 模块划分
*   **UI Layer (PySide6)**:
    *   **ChatWindow**: 负责展示对话历史和接收用户输入。
    *   **WorkerThread**: 在后台线程执行耗时任务（LLM 请求、代码执行），防止界面卡顿。
*   **Core Logic**:
    *   **Agent**: 负责与 LLM 交互，维护上下文。
    *   **Code Executor**:
        *   直接利用打包好的 Python 环境运行生成的代码。
        *   使用 `subprocess` 或 `exec` (带安全限制) 执行。
        *   **优势**: 原生支持所有已安装的 Python 库 (pandas, openpyxl, etc.)，无需 WASM 桥接。

---

## 6. 安全机制 (Security & Sandboxing)

为了防止 AI 生成的恶意代码或误操作损害用户系统，必须实施严格的权限控制。

### 6.1 目录权限控制 (Workspace Restriction)
*   **原则**: Agent 只能读取和写入用户显式授权的目录（"Workspace"）。
*   **实现**:
    1.  **UI 交互**: 用户必须先点击 "选择工作区" 按钮指定一个文件夹。
    2.  **执行环境**: `subprocess.run(cwd=workspace_path)`，确保相对路径操作限定在工作区内。
    3.  **静态分析 (AST Check)**: 在代码执行前，解析 Python 抽象语法树 (AST)。
        *   扫描所有字符串字面量。
        *   如果发现绝对路径（如 `C:/Windows`）或向上越级路径（`../`），且该路径不在授权范围内，则直接拦截并报警。

## 7. 架构：Model Context Protocol (MCP) 与 Skills

为了增强扩展性并遵循行业标准，我们采用了 **MCP (Model Context Protocol)** 理念来组织代码。

### 7.1 核心概念
*   **MCP (Model Context Protocol)**: 由 Anthropic 提出的开放标准，用于连接 AI 助手与系统（数据、工具、Prompt）。
    *   **MCP Server**: 通过标准化的接口暴露资源（文件、日志）和工具（函数）。在本项目中，`ToolManager` 充当内部 MCP Server 的角色。
    *   **MCP Client**: 即我们的 AI 应用（Agent），连接到 Server 以发现并使用这些能力。
*   **Skills (技能)**: 代表一个离散的能力单元（例如 `FileSystemSkill`, `GitSkill`）。在 MCP 术语中，这些是 Server 暴露的 **Tools** 集合。

### 7.2 实现状态
*   **Skills 目录**: 所有能力都以标准化 Skill 包的形式存在于 `skills/` 目录下。
*   **ToolManager**: 负责扫描、加载和管理这些 Skill，并为 Agent 提供统一的调用接口 `call_tool(name, args)`。
*   **LLMWorker**: 作为 Client，通过 ToolManager 获取工具定义并执行操作。

### 7.3 技能规格规范 (Skills Specification)

为了标准化我们在项目中定义能力（Skills）的方式，我们采用了受行业实践启发的目录结构。

#### 目录结构
一个 Skill 是一个目录，至少包含一个 `SKILL.md` 文件：
```
skills/
└── file-system/
    ├── SKILL.md          # 定义与 Prompts
    ├── impl.py           # Python 实现代码
    └── scripts/          # 辅助脚本（如果有）
```

#### SKILL.md 格式
文件包含 YAML 前置元数据（Frontmatter），后跟 Markdown 内容（系统提示词上下文）。

```yaml
---
name: file-system
description: 提供在用户工作区中列出、读取和写入文件的能力。
license: Apache-2.0
metadata:
  author: cowork-team
  version: "1.0"
allowed-tools: list_files read_file write_file
---

# File System Skill

此技能允许 Agent 与授权工作区内的本地文件系统进行交互。

## 使用指南
- 在读取文件之前，务必先检查文件是否存在。
- 禁止访问工作区之外的文件（以 `..` 开头的路径或绝对路径将被禁止）。
```

这种标准化使得我们能够轻松扩展 Agent 的能力，只需添加新的 Skill 目录即可。


