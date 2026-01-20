# Agent 交错思维链架构设计 (Technical Design)

## 1. 核心理念：DeepSeek-V3.2 Interleaved Chain-of-Thought

本项目基于 **DeepSeek-V3.2** 模型构建，核心创新在于采用了 **交错思维链 (Interleaved CoT)** 架构。这与传统的 Agent 架构（如 ReAct 或标准 CoT）有着本质区别。

### 1.1 传统模式的局限
*   **ReAct (Reason+Act)**: 模型在每一步都必须停止思考，输出一个 Action，等待结果，然后再思考。这种频繁的上下文切换打断了推理的连贯性，且耗时较长。
*   **Linear CoT (标准思维链)**: 模型必须一次性完成所有思考，然后生成最终代码。如果思考过程中缺乏关键信息（例如不知道某个文件的具体字段名），模型往往会产生幻觉（Hallucination），导致生成的代码无法运行。

### 1.2 DeepSeek-V3.2 的突破：思考融入工具调用
DeepSeek-V3.2 允许在 `<think>` 阶段直接发起工具调用。
*   **流程**: `Think -> Tool Call -> Tool Result -> Continue Thinking -> Final Answer`
*   **优势**:
    1.  **动态探索**: 模型可以在思考中途发现信息缺失，主动调用工具（如 `read_file`）获取信息，然后基于真实数据修正后续计划。
    2.  **自我纠错**: 如果某一步思考发现逻辑矛盾，可以立即验证，而不是等到最后代码运行失败才发现。
    3.  **高效**: 相比 ReAct，减少了多次 HTTP 请求的往返开销（Token 效率更高）。

---

## 2. 架构组件 (Architecture Components)

### 2.1 Agent Core (Interleaved CoT Engine)
*   **Interaction Loop**:
    1.  **User Input**: 接收用户指令。
    2.  **Model Inference**: 调用 DeepSeek API (Thinking Mode)。
    3.  **Stream Parsing**: 实时解析流式响应。
        *   监测 `reasoning_content` 中的特殊标记或结构化数据，识别工具调用请求。
        *   注意：虽然 V3.2 API 协议上可能在 `content` 或特定字段返回工具调用，但在 UI 展示上，我们将这些“思考过程中的动作”渲染在 Task Monitor 中。
    4.  **Tool Execution**:
        *   当检测到工具调用时，暂停模型生成（或模型自动暂停）。
        *   执行工具（如 `list_files`）。
        *   将结果（Tool Output）回传给模型。
    5.  **Continue Inference**: 模型接收工具结果，继续 `<think>` 或输出最终回答。

*   **Prompt Strategy**:
    *   System Prompt 针对 V3.2 优化，强调“在不确定的情况下，先调用工具探索，再下结论”。
    *   利用大规模 Agent 训练数据（1800+ 环境，85,000+ 复杂指令）带来的泛化能力，减少对复杂 Prompt Engineering 的依赖。

### 2.2 UI Layer (PySide6)
*   **Task Monitor (思维链可视化)**:
    *   不仅仅展示文本日志，而是结构化展示“思考节点”和“动作节点”。
    *   **Thinking Node**: 显示 `<think>` 内容。
    *   **Action Node**: 显示工具调用及结果（如 `ls -> ['a.txt']`）。
    *   用户可以清晰看到模型是如何一步步解决问题的。
*   **Chat Bubble**: 仅展示最终的 `content`，保持界面清爽。

### 2.3 Executor (执行器)
*   **安全沙箱**: 所有的工具调用（无论是思考过程中的探索，还是最终的执行）都受到严格限制。
    *   **Read-Only Tools**: 在思考阶段，模型倾向于使用只读工具（`list_files`, `read_file`）来收集信息。
    *   **Write Tools**: 修改操作（`write_file`, `delete_file`）通常在思考成熟后，或在用户确认后执行。

---

## 3. 数据流示例 (Data Flow Example)

**用户输入**: "读取 config.json，帮我把里面的 'port' 改成 8080。"

**DeepSeek-V3.2 Interleaved Flow:**

1.  **Think**: 用户想修改配置。我不知道 config.json 在哪里，也不知道它的结构。
2.  **Action (in Think)**: `list_files(".")`
3.  **Observation**: `['src/', 'config.json', 'README.md']`
4.  **Think**: 找到了 config.json。现在我要读取它确认格式。
5.  **Action (in Think)**: `read_file("config.json")`
6.  **Observation**: `{"host": "localhost", "port": 3000}`
7.  **Think**: 确认了，当前 port 是 3000。我需要用 json 模块加载它，修改为 8080，然后写回。
8.  **Final Code Generation**: 
    ```python
    import json
    with open('config.json', 'r') as f:
        data = json.load(f)
    data['port'] = 8080
    with open('config.json', 'w') as f:
        json.dump(data, f, indent=2)
    ```
9.  **Execution**: 运行代码 -> 修改成功。
10. **Final Answer**: "已将 config.json 中的端口修改为 8080。"

---

## 4. 关键技术指标

*   **推理能力**: 达到 GPT-5 水平，略低于 Gemini-3.0-Pro。
*   **Agent 泛化**: 在大规模强化学习任务（1800+ 环境）上训练，具备极强的指令跟随能力。
*   **响应速度**: 相比 Kimi-K2-Thinking，输出长度大幅降低，响应更快。
