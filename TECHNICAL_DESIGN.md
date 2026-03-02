# DeepSeek Cowork 架构设计

项目团队：**deepseek-cowork team**。

## 1. 架构理念

DeepSeek Cowork 采用 **Interleaved Chain-of-Thought** 架构，在推理阶段直接调用工具，实现“思考-执行-再思考”的闭环，降低幻觉并提升任务成功率。

## 2. 核心组件

### 2.1 UI 层 (PySide6)
*   **main.py**：桌面入口，负责窗口、聊天气泡、工具调用卡片、工作区侧边栏等 UI 交互。
*   **可视化监控**：展示子任务状态与思考过程。

### 2.2 Agent Core
*   **core/agent.py**：推理循环与工具调度，负责将用户输入转化为可执行任务。
*   **core/interaction.py**：桥接 UI 与推理流程，统一消息与工具调用格式。

### 2.3 Daemon 与并发
*   **core/daemon.py**：无头推理服务，分离 UI 与模型推理负载。
*   **QThread**：UI 与后台线程解耦，保证界面响应。

### 2.4 技能系统
*   **core/skill_manager.py**：加载 `skills/` 与 `ai_skills/`，注入工具定义与经验。
*   **经验回写**：执行结果可回写到 `SKILL.md`，形成自进化闭环。

### 2.5 配置与存储
*   **core/config_manager.py**：统一配置入口，管理 API Key、Provider、工作区等设置。
*   **core/chat_storage.py**：历史对话持久化与按日归档。

### 2.6 企业 IM
*   **core/im_gateway.py**：飞书长连接网关，接收 IM 事件并回传执行结果。
*   **会话映射**：IM 会话与本地会话保持一致的工作区边界。

## 3. 数据流 (Data Flow)

1.  用户在 UI 或 IM 中输入指令。
2.  UI 将指令转交给 Daemon 的推理线程。
3.  Agent 进入 Interleaved CoT 流程：
    *   读取环境与文件（只读工具）。
    *   生成执行计划并调用写工具。
4.  工具结果回传给 Agent，完成最终回复。
5.  UI 渲染聊天气泡、工具调用卡片与状态变化。

## 4. 运行模式与环境

*   **源码模式**：建议使用虚拟环境 **.venv\Scripts\python** 启动。
*   **可执行模式**：PyInstaller 打包后由 `env_utils` 自动定位 Python 与 pip。

## 5. 目录结构

*   **core/**：推理、配置、守护进程、IM 网关等核心逻辑
*   **skills/**：内置系统技能
*   **ai_skills/**：AI 或用户创建技能
*   **main.py**：桌面 UI 入口
