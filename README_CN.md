# DeepSeek Cowork

[中文文档](README_CN.md) | [English](README.md)

**DeepSeek Cowork** 是围绕 **DeepSeek V4 thinking 与工具调用工作流** 构建的 Windows 桌面智能代理框架。它将推理与工具调用融合在一个连续流程中，面向文件、应用与工作流提供稳定可控的自动化能力。（**本项目不是DeepSeek官方开发，纯个人探索和爱好**）

项目团队：**deepseek-cowork team**。

当前应用版本：**4.8.3**。

![应用截图1](images/首页.png)
![应用截图2](images/使用界面.png)

## 🚀 核心特性

### 🧠 推理与工具调用融合
*   **交错式 CoT**：思考中调用工具、观察结果、继续推理，减少幻觉。
*   **工具先探测**：先读取真实文件与环境，再做执行决策。

### 🔌 技能系统
*   **经验优先的技能**：技能被视为结构化经验包，而不是第二套执行协议。
*   **热重载技能**：将新技能放入 `skills/` 或 `ai_skills/`，无需重启即可使用。
*   **可迁移能力包**：Skill 支持导出为 ZIP，也支持从 ZIP 文件或源码文件夹回导。
*   **结构化经验沉淀**：运行中的 lessons learned 可以先写入结构化 entry，再同步回 `SKILL.md` 摘要。
*   **会话沉淀为 Skill**：点击 `沉淀为 Skill` 可将当前会话提炼为可审阅草稿，用于新建 Skill 或更新已有 Skill。
*   **显式只读并行工具**：`parallel_tools` 可并发执行彼此独立的只读工具调用，保持结果顺序，并拒绝写入、审批、命令执行等非只读操作。

### 🖥️ 桌面体验
*   **PySide6 UI**：更克制的蓝白桌面界面，包含气泡对话、Markdown 渲染、工具调用卡片，以及会随窗口动态调整、接近 Codex 阅读比例的对话列。
*   **项目式左侧栏**：左侧栏以本地文件夹作为项目，顶部固定 `新建对话` 与搜索；点击项目即切换工作区，项目默认折叠并只预览少量会话，点 `展开显示` 后再展开完整列表；右上角不再提供独立工作区切换按钮，整体视觉改为更柔和的 Apple 风格面板，项目行操作按钮也保持低干扰的轻量样式。
*   **右侧上下文抽屉**：文件、自动化步骤、任务观测、子 Agent 状态通过紧凑图标按钮按需展开；子 Agent 活动只点亮面板提示，不强制打开抽屉。
*   **任务观测 Prompt 预览**：系统提示词页会优先展示后续 append 的系统消息，长基础 prompt 默认隐藏前半段；需要排查完整上下文时可一键复制全量系统提示词。
*   **图片理解附件**：输入区添加的图片会保留结构化附件信息，只有当前模型开启 `支持图片理解` 时才会作为多模态输入发送。
*   **自动化中心**：左侧边栏提供独立 `自动化` 按钮，可查看已配置任务、执行历史和任务模板；定时任务同时支持 crontab 表达式和快捷配置。
*   **会话级控制**：输入区可将文件作为“用户添加的文件”附件 chip 添加，插入智能体、绑定自动化模板、指定本会话可用能力或开启反问模式；已绑定的自动化和已选能力 chip 可一键移除。
*   **对话分支**：已完成的用户/助手气泡提供轻量 `分支` 按钮，可从该消息位置创建新的会话分支；新会话会继承当前项目和已选能力，并在元信息中记录来源会话与来源消息。用户消息还支持“编辑后重新生成”和“删除并继续”，两者都会创建新分支，而不是改写原始会话。
*   **系统状态提示条**：聊天流里的系统提示改为更轻的 Apple 风格状态条，使用低饱和底色、短文案和自动消失，而不是整块高对比通知框。
*   **多分身监控**：在当前任务内以轻量时间线摘要查看并行子任务的输入、工具调用、工具结果、流式输出与最终输出；打开面板后会在主线程短延迟批量渲染，保持 UI 稳定。
*   **安全的子 Agent 生命周期**：子 Agent 的结果落库、状态流转与工作线程回收分离，避免并行任务完成或续跑时影响桌面应用稳定性。
*   **设置保存更顺滑**：设置对话框会把模型、MCP、智能体、工作区和企业消息配置合并为一次落盘，减少连续保存导致的界面卡顿。
*   **子 Agent 运行日志**：生命周期诊断会写入 `DeepSeekCowork` 应用数据目录下的 `sub_agent_runtime.log`；便携模式则写入 `user_data/`。
*   **手动反馈入口**：侧边栏提供 `更新长期记忆` 与 `沉淀为 Skill`，在人确认后再保存可复用知识。

### 🛰️ 守护进程与 IM 网关
*   **无头守护进程**：后台推理保证 UI 轻量响应。
*   **企业 IM (飞书 / 钉钉 / 企业微信智能机器人)**：通过企业消息下发任务并按日归档。
*   **上下文预算管理**：IM 会话按模型能力管理上下文；DeepSeek V4 默认利用 1M token 长窗口，接近预算才压缩，小窗口模型仍保持保守压缩，并在溢出时自动重试一次。
*   **工作区约束**：未开启 God Mode 时，IM 指令同样受工作区限制。

## 📦 安装指南

### 选项 1：运行可执行文件 (Windows)
1.  前往 [Releases](../../releases) 下载最新版。
2.  解压并运行 `deepseek-cowork.exe`。
3.  无需安装 Python。

### 选项 2：源码运行 (Windows)
**前置要求**：Python 3.10+

1.  克隆仓库：
    ```bash
    git clone https://github.com/chuancyzhang/deepseek-cowork.git
    cd deepseek-cowork
    ```

2.  创建并使用虚拟环境：
    ```bash
    python -m pip install -r requirements.txt
    ```

3.  启动应用：
    ```bash
    python main.py
    ```

### 打包前运行时准备（Windows）
在执行 `pyinstaller deepseek-cowork.spec` 之前，先拉取固定版本的运行时包（Node.js + Git Bash），并进行 SHA256 校验：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\fetch_runtimes.ps1
```

下载文件会放在 `.runtime_downloads/`，解压后目录为 `node_env/` 与 `git_bash_env/`。
Windows 打包版会优先直接从当前应用目录解析内置运行时，包括 `_internal/node_env/node.exe` 与 `_internal/git_bash_env/bin/bash.exe`。AppData 下的 `runtime_sandbox` 继续作为临时、缓存和 skill 依赖目录，不再优先作为可执行运行时来源，因此自动升级后会使用新解压的 `_internal` 文件。如需覆盖探测结果，可设置 `COWORK_NODE_EXE`、`COWORK_NODE_DIR`、`COWORK_BASH_EXE`、`COWORK_GIT_BASH_DIR` 或 `COWORK_BASH_DIR`。
AI 在 execution 模式下默认可直接调用 `run_python_code`，无需先通过 `tool_search` 发现；JavaScript 仍通过 `run_node_code` 执行，`bash` 工具优先使用 Git Bash，Windows 上缺失 Git Bash 时会退回 `cmd.exe`。
`python-runner` 的 `install_package` 会在与 `run_python_code` 相同的沙盒运行时里验证导入是否成功。第三方包会持久化到 AppData 的 `runtime_sandbox/.../skills/python-runner/python/site-packages`，重启后仍可用；如果依赖缓存记录还在但沙盒已经无法导入，会自动触发重新安装。
沙盒现在会注入 Python bootstrap `sitecustomize.py`、`PATH` 与 `COWORK_PYTHON_DLL_DIRS`，让 Windows 下带原生扩展的包可以从内置运行时和 skill 专属 `site-packages` 解析 DLL。
如果安装后仍然导入失败，`install_package` 会返回沙盒里的真实 traceback，而不再只给出笼统的 “still cannot import” 提示。
Windows 打包时还必须把 `DLLs/` 这类平台扩展目录以及常见 MSVC runtime DLL 一并带入内置 Python；否则 `_socket` 等核心扩展缺失，`_internal/python_env` 内的 `pip` 或原生包都可能无法加载。

## 📖 使用指南

### 1. 配置
打开 **⚙️ 设置**：
*   **API Key**：DeepSeek 或其他模型提供商密钥。
*   **Provider**：`openai`（兼容 DeepSeek）或 `anthropic`。
*   **MCP**：可配置 `stdio` 或 Streamable HTTP 的 MCP 服务器；启用后会作为延迟发现的外部工具接入 `tool_search`。
*   **God Mode**：安全沙箱开关。

### 2. 选择项目
在左侧栏的 **项目** 区添加或选择本地文件夹。项目就是工作区，Agent 只能在当前项目范围内读写文件；新建对话会默认归属当前项目。

### 3. 开始自动化
示例：
*   *“扫描这个项目中未使用的 import 并移除。”*
*   *“汇总文件夹里的所有 PDF 并生成报告。”*
*   *“创建一个使用 yt-dlp 的视频下载技能。”*
*   输入区 `+` 菜单中的 **从对话生成 SOP** 会从当前会话一次性提炼完整 SOP 草稿；确认后保存为任务模板并绑定到当前会话，也可以输入修改意见重新生成草稿。

### 4. 管理自动化
打开左侧边栏的 **自动化**：
*   **已配置**：创建定时自动化任务，启用或暂停任务，查看下次执行时间，或立即运行；支持直接填写 5 段 crontab 语法，也支持每天/每周/每月/间隔/单次的快捷配置。
*   **执行历史**：查看已完成、失败、中断或错过的自动化运行，并重新打开关联任务会话。
*   **任务模板**：继续使用原来的 SOP 模板配置逻辑，但不再暴露手工 ID，系统会自动生成；模板和步骤都可配置人工确认或自动推进，步骤执行器支持 Agent、上传 Python 文件和 Bash 命令。

### 5. 闭合反馈回路
在完成有价值的会话后，可以使用侧边栏入口：
*   **`更新长期记忆`**：扫描新增或变更的历史会话，分批合并写入 `memories.md`；支持进度展示、缩小到后台、结果预览、手动编辑与再次保存。
*   **`沉淀为 Skill`**：将当前会话生成 Skill 草稿，可新建 Skill，也可对已有 Skill 追加经验或重写说明；保存前可预览和编辑。
*   **功能中心导入/导出**：可从文件夹或 ZIP 导入自定义能力，也可将现有 Skill 导出为可迁移 ZIP 包。

### 6. 企业消息
打开 **⚙️ 设置 → 企业消息**，在飞书、钉钉或企业微信智能机器人分组中填写对应凭据，启用渠道并启动网关。

### 7. 应用更新
打开 **⚙️ 设置 → 更新** 检查 GitHub Releases。打包版会下载、校验、解压暂存并通过独立更新器重启安装；源码运行模式只检查版本并提供 Releases 页面入口。

### 8. MCP 服务器
打开 **⚙️ 设置 → MCP** 添加 MCP 服务器。
*   打包版默认内置 MCP client 运行时，终端用户无需再单独安装 `mcp` 包。
*   `stdio`：配置命令、参数、工作目录、环境变量和启动超时。
*   Streamable HTTP：配置 URL、Headers 和启动超时。
*   也支持直接导入 JSON 配置，例如 `{"mcpServers":{"showdoc":{"type":"streamable-http","url":"https://example.com/mcp","headers":{"Authorization":"Bearer ..."}}}}`。
*   当前首版只接入 **tools**，暂不支持 MCP resources 和 prompts。

## 🏗️ 架构概览

*   **`main.py`**：PySide6 桌面 UI 入口。
*   **`core/agent.py`**：推理循环与工具调度。
*   **`core/daemon.py`**：无头推理服务。
*   **`core/mcp_client.py`**：MCP `stdio` / Streamable HTTP 传输桥接与工具调用封装。
*   **`core/im_gateway/`**：多平台企业消息网关与渠道适配。
*   **`core/skill_manager.py`**：工具注册、经验包加载、相关性匹配与 Prompt 注入。
*   **`core/sop_manager.py`**：会话级自动化模板、步骤状态、推进方式（人工确认 / 自动推进）、确认、重跑与跳过逻辑。
*   **`core/sop_from_conversation.py`**：从当前会话生成可预览、可修订的 SOP 草稿。
*   **`core/automation_manager.py`**：定时自动化任务的规范化、cron / 快捷计划的下次执行时间计算、执行提示词拼装与历史记录辅助函数。
*   **`core/updater.py`**：GitHub Releases 检查、安装包校验、暂存和 Windows 重启更新器。
*   **`skills/`**：内置系统技能。
*   **`ai_skills/`**：AI 或用户创建的技能。

## 🧩 Tool + Experience 模型
- `tool` 是模型唯一直接调用的执行面。
- 工具源于技能目录中的公开 Python 函数（`impl.py`），自动生成 JSON Schema 并被模型调用。
- `skill` 是结构化经验包，承载边界、坑点、经验、推荐流程与推荐工具。
- 新经验默认可先写入结构化 entry，再回写到 `SKILL.md` 摘要。
- `沉淀为 Skill` 是人工确认的沉淀入口，会把会话经验转化为 `SKILL.md`、`skill.json`、`experience/entries.jsonl` 与可选 `impl.py`。
- Skill 可以导出为 ZIP 包；ZIP 导入会校验安全路径，支持平铺根目录或单文件夹根目录，优先沿用元数据中的原 Skill 名称，并拒绝同名覆盖。
- 新技能仍然支持热加载，无需重启。
- 当前源码已打通“提示模型记录经验 -> 调用 `update_experience` -> 持久化经验 -> 后续任务再次注入”的链路，因此 AI 在合适场景下可以主动调用经验工具。
- 但这仍属于可用的第一版经验闭环：系统会鼓励并支持 AI 记录经验，不保证每次都会主动调用，也尚未内建经验验证、效果评估、生命周期治理等强反馈机制。

## 🔄 Agentic Workflow
- 交错式 CoT：思考 → 工具调用 → 观察 → 继续思考 → 最终回答。
- 流式事件：思考/内容/工具调用/工具结果实时回传 UI；子 Agent 还会额外上报输入、工具结果与完成态事件，供右侧时间线可视化。
- 子 Agent 生命周期将结果处理与 Qt 线程清理拆开，旧 worker 完全退出后才会续跑排队输入。
- 子 Agent 监控事件先收集到会话队列，用户打开抽屉后再以轻量摘要行渲染，避免流式过程中频繁构造控件。
- 右侧上下文抽屉的外部点击关闭逻辑改为结合全局坐标命中判断，子 Agent 面板内部子控件、滚动区域 viewport 和临时弹层不会再被误判为抽屉外点击。
- 子 Agent 抽屉的显示与隐藏原因会继续写入 `sub_agent_runtime.log`，方便排查意外收起或闪退前的最后一步。
- 环路保护：检测重复思考或重复工具签名并自动停止。
- 支持暂停/恢复/停止，安全管控长任务。
- 会话自动化模板会把执行约束在当前步骤内；模板和步骤都可以配置为人工确认或自动推进，系统每次只派发当前步骤而不是整段 SOP prompt。步骤执行器支持 Agent、上传后的 Python 文件和 Bash 命令。
- 定时自动化任务会复用同一套模板配置，按步骤创建独立执行轮次；遇到人工确认步骤时会暂停到“等待确认”，其余步骤可自动连续推进，并把每次运行写入本地执行历史。计划配置支持应用内 cron 语法，不依赖系统 crontab。
- 反问模式仅开放只读探索，并通过交互工具收集关键问题答案后再回到正常执行。

## 🧠 分层记忆与上下文
- System Prompt 排序：稳定策略和工具使用规则靠前，运行模式、日期、运行时路径、指定能力、SOP 当前步骤等易变状态靠后，以提升 DeepSeek 上下文缓存稳定性。
- 记忆层：可选 `memories.md` 自动注入，承载稳定偏好与长期信息；在架构上，记忆被视为经验系统中的长期层，而不是独立于经验系统之外的另一套协议。
- 长期记忆通过 `更新长期记忆` 手动触发，系统用 `memories_update_state.json` 记录已处理历史，后续更新聚焦新增或变更会话。
- 技能提示：先注入最小经验摘要，需要时再展开完整说明。
- 会话级指定能力与智能体配置会收窄当前任务的可用能力范围。
- 上下文预算：DeepSeek V4 Pro/Flash 默认使用 1,000,000 token 窗口，仅在接近配置阈值时主动压缩；非 V4 模型使用更保守的小窗口预算。
- 历史清洁：每轮清理/折叠思考内容，并保留 DeepSeek thinking 工具调用回放所需的 `reasoning_content` 字段。

## 🛠️ 扩展开发
在 `skills/` 新建文件夹：
1.  `SKILL.md`：经验包正文。
2.  `skill.json`：能力分组、tool refs、workflow、披露默认值等元数据。
3.  可选 `impl.py`：当技能需要新增可调用工具时使用。
4.  可选 `experience/entries.jsonl`：结构化运行经验。

推荐心智模型：
- `tool` = 执行器
- `skill` = 经验包

完整说明见 [SKILL_SYSTEM.md](SKILL_SYSTEM.md)。

## 📄 许可证

MIT License
