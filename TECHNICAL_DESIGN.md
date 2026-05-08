# DeepSeek Cowork 架构设计

项目团队：**deepseek-cowork team**。

## 1. 架构理念

DeepSeek Cowork 采用 **Interleaved Chain-of-Thought** 架构，在推理阶段直接调用工具，实现“思考-执行-再思考”的闭环，降低幻觉并提升任务成功率。

## 2. 核心组件

### 2.1 UI 层 (PySide6)
*   **main.py**：桌面入口，负责窗口、聊天气泡、工具调用卡片、工作区侧边栏等 UI 交互。
*   **可视化监控**：展示子任务状态与思考过程。
*   **反馈回路按钮**：侧边栏 `更新长期记忆` 与 `沉淀为 Skill` 触发后台 worker，并在 UI 中提供进度、预览、编辑与保存确认。

### 2.2 Agent Core
*   **core/agent.py**：推理循环与工具调度，负责将用户输入转化为可执行任务。
*   **core/interaction.py**：桥接 UI 与推理流程，统一消息与工具调用格式。

### 2.3 Daemon 与并发
*   **core/daemon.py**：无头推理服务，分离 UI 与模型推理负载。
*   **QThread**：UI 与后台线程解耦，保证界面响应。

### 2.4 技能系统
*   **core/skill_manager.py**：加载 `skills/` 与 `ai_skills/`，注入工具定义与经验。
*   **经验回写**：执行结果可回写到 `SKILL.md`，形成自进化闭环。
*   **core/skill_from_conversation.py**：把当前会话转录为可复用 Skill 草稿，并负责新建或更新 Skill 文件。

### 2.5 配置与存储
*   **core/config_manager.py**：统一配置入口，管理 API Key、Provider、工作区等设置。
*   **core/chat_storage.py**：历史对话持久化与按日归档。
*   **core/memory_update.py**：扫描历史会话，分批更新 `memories.md`，写入备份与 `memories_update_state.json`。

### 2.6 企业 IM
*   **core/im_gateway/**：多平台企业消息网关，接收飞书、钉钉与企业微信智能机器人事件并回传执行结果。
*   **会话映射**：IM 会话与本地会话保持一致的工作区边界，并按 provider 区分会话来源。

## 3. 万物皆工具 (Everything Is a Tool)
- 工具即 `impl.py` 中的函数，解析签名动态生成 JSON Schema，作为 LLM 可调用的函数接口。
- `SKILL.md`：前言 (frontmatter) 提供元数据与 allowed-tools，正文提供使用指引；`experience` 字段承载自进化经验并在调用前注入。
- 动态导入与依赖自修复：缺失依赖时尝试自动安装并重试加载，提升技能首用成功率。
- 工具到技能映射：用于 UI 上报与提示注入。

## 3. 数据流 (Data Flow)

1.  用户在 UI 或 IM 中输入指令。
2.  UI 将指令转交给 Daemon 的推理线程。
3.  Agent 进入 Interleaved CoT 流程：
    *   读取环境与文件（只读工具）。
    *   生成执行计划并调用写工具。
4.  工具结果回传给 Agent，完成最终回复。
5.  UI 渲染聊天气泡、工具调用卡片与状态变化。

### 3.1 手动反馈回路数据流

**长期记忆更新**
1.  用户点击 `更新长期记忆`。
2.  `MemoryUpdateWorker` 从 `core/chat_storage.py` 读取新增或变更的历史会话，并跳过 `memories_update_state.json` 中已处理的内容。
3.  `core/memory_update.py` 按批构造提示，将当前 `memories.md` 与历史批次交给模型合并；当模型返回为空或失败时执行有限重试。
4.  每个批次写入 `memories.md` 并生成备份，同时向对话框回传进度和批次预览；最终结果仍允许用户编辑并再次保存。

**会话沉淀为 Skill**
1.  用户点击 `沉淀为 Skill`。
2.  `ConversationSkillDraftWorker` 将当前会话渲染为转录文本，调用 `core/skill_from_conversation.py` 生成草稿。
3.  用户选择新建 Skill，或更新已有 Skill 的追加经验/重写说明策略。
4.  保存时写入 `SKILL.md`、`skill.json`、`experience/entries.jsonl` 与可选 `impl.py`，然后重新加载技能。

## 4. 分层记忆与上下文处理
- **系统层**：工作区、OS、Python 路径、日期、操作规范等基础上下文。
- **记忆层**：`memories.md`（可选）承载稳定偏好与长期信息，自动注入 System Prompt；`更新长期记忆` 通过 `memories_update_state.json` 记录处理进度，后续运行聚焦新增或变更会话。
- **技能层**：首次调用技能时注入简版能力提示；按需注入技能完整说明与经验。
- **历史层**：每轮清理/折叠思考内容以避免重复；仅保留必要字段满足 API 要求。

## 4. 运行模式与环境

*   **源码模式**：建议使用虚拟环境 **.venv\Scripts\python** 启动。
*   **可执行模式**：PyInstaller 打包后由 `env_utils` 自动定位 Python 与 pip。

## 5. 动态技能加载与自我进化
- **更新检测**：对 `SKILL.md`/`impl.py` 的修改时间进行检测，晚于上次加载则触发热加载。
- **热加载**：重置工具注册与提示集合，重新解析并加载实现。
- **经验写回**：通过 `update_skill_experience` 追加经验到 `SKILL.md` 的 `experience` 字段，形成“执行—学习—再执行”的闭环。
- **人工沉淀**：`沉淀为 Skill` 是显式确认通道，会话先生成草稿并由用户预览编辑，再写入新 Skill 或更新已有 Skill。

## 6. 状态机流转 (Agentic Workflow)
- **状态**：Idle → Thinking → ToolCalling → Observing → Answering → Completed。
- **信号**：`thinking_signal`、`content_signal`、`tool_call_signal`、`tool_result_signal`、`agent_state_signal`。
- **控制**：`pause`、`resume`、`stop`；环路保护（重复思考/工具签名）确保安全收敛。
- **实现要点**：流式解析四类事件，按需注入技能提示，结果写入历史后继续下一轮直至最终回答。

## 5. 目录结构

*   **core/**：推理、配置、守护进程、IM 网关等核心逻辑
*   **skills/**：内置系统技能
*   **ai_skills/**：AI 或用户创建技能
*   **main.py**：桌面 UI 入口
