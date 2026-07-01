# DeepSeek Cowork

[中文文档](README_CN.md) | [English](README.md)

DeepSeek Cowork 是一个 Windows 桌面 Agent 工作台，把对话、项目工作区、工具调用、技能扩展、自动化和可选后台执行整合到同一个 PySide6 应用里。

本项目不是 DeepSeek 官方产品，仅为个人探索。

当前应用版本：**4.9.5**

## 它能做什么

- 在桌面端运行可读文件、可调工具、可执行多步骤任务的本地 Agent。
- 通过项目绑定把文件操作限制在指定工作区内。
- 通过内置技能、随包可选插件、自定义技能和 MCP 工具扩展能力。
- 在 UI 中统一管理模型、智能体、自动化、长期记忆和运行组件。
- 直接预览 Markdown、HTML、图片、PDF、DOCX、PPTX、XLSX 等交付物。
- 在 AI 回复末尾点击 **生成办公稿**，把当前回复转成可预览、可迭代的自由、PPT、设计稿或 DOCX 型办公交付物；生成过程默认折叠为任务卡，结果文件直接显示在卡片下方。
- 从 HTML 继续生成 PPTX、DOCX 或 PDF 时只给局部生成反馈，右侧抽屉仍可浏览、关闭或继续预览；完成后通过 Toast 和交付物入口回到结果文件。

## 产品界面

- **对话 + 项目模型**：可直接开始纯对话，也可把对话绑定到项目工作区。
- **可靠历史加载**：当前格式会话保存在 SQLite 中；打开历史会话前会先等待同会话保存队列落盘，读取失败会在界面提示而不是显示为空会话。早期文本文件历史不再作为当前加载兼容目标。
- **必要时澄清**：AI 默认直接执行；只有关键意图不清会导致执行错误或明显风险时，才用选项卡片低打扰澄清，未响应时采用推荐选项继续。
- **生成办公稿**：在 AI 回复末尾选择自由、PPT、设计稿或 DOCX，把内容沉淀为可预览工作稿，生成轮次默认收起为任务卡，交付物入口保持外显；基于 HTML 继续生成 PPTX、DOCX、PDF 的过程也保持折叠。
- **右侧上下文抽屉**：用浏览态查找文件和交付物，点击后下钻到专注预览与操作态；对话里的交付物卡片会直接打开预览。
- **Token 用量浮层**：会话顶部显示本对话累计 token 与缓存输入用量，点击后以轻量浮层查看明细。
- **设置中心**：管理模型、智能体、默认工作区、MCP、企业消息和组件依赖。
- **自动化中心**：管理任务模板、定时计划、执行历史和人工确认步骤。
- **技能中心**：启用、导入、导出、调试和校验技能，无需重启。

## 技能模型

Cowork 只把 `tool` 当作直接执行面：

- `tool`：模型可直接调用的执行能力
- `skill`：指导工具选择与组合方式的经验包

技能既可以只提供经验，也可以同时携带工具实现。内置技能位于 `skills/`，随包插件和用户技能位于 `ai_skills/`。Cowork 可以安装符合标准的 Agent Skill 包，保留原始根目录 `SKILL.md`，只生成本地检索、工作台和调试所需的 `skill.json`。技能可以在 `skill.json` 声明 `config_fields`，工作台会自动生成“配置”页，并把保存的值按字段声明注入到脚本或工具运行环境。

完整说明见 [SKILL_SYSTEM.md](SKILL_SYSTEM.md)。

## 安装

### Windows 可执行文件

1. 从 [Releases](../../releases) 下载最新版。
2. 解压。
3. 运行 `deepseek-cowork.exe`。

### 源码运行

前置要求：Python 3.10+

```bash
git clone https://github.com/chuancyzhang/deepseek-cowork.git
cd deepseek-cowork
python -m pip install -r requirements.txt
python main.py
```

### 打包前运行时准备

执行 `pyinstaller deepseek-cowork.spec` 之前，先拉取固定运行时：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\fetch_runtimes.ps1
```

该脚本会准备打包所需的 Git Bash 运行时。Node.js 改为在设置中按需安装的可选组件。

## 基本使用

1. 打开 **设置**，先配置模型服务。
2. 新建纯对话，或把对话连接到某个项目。
3. 让 Agent 读取文件、修改代码、生成报告或执行自动化；需要办公交付物时，可在 AI 回复末尾点击 **生成办公稿** 并选择类型。
4. 在 **技能中心** 启用需要的可选能力，或通过 `install_agent_skill` 安装符合标准的 Agent Skill；腾讯文档、飞书文档、钉钉文档已作为三套独立文档 skill 内置，工作台可查看凭据配置、只读文件、Tool 调试和脚本入口。
5. 用右侧抽屉浏览文件、预览交付物、转换 HTML 工作稿并查看工具执行过程。
6. 当某段流程有复用价值时，用 **记忆** 或 **沉淀为 Skill** 固化下来。

## 架构入口

- `main.py`：PySide6 桌面 UI
- `core/agent.py`：推理循环与工具调度
- `core/daemon.py`：可选后台执行路径
- `core/skill_manager.py`：技能加载、披露和工具注册
- `core/mcp_client.py`：MCP `stdio` / Streamable HTTP 集成
- `core/sop_manager.py`：自动化模板与步骤状态
- `core/automation_manager.py`：定时任务与运行历史
- `core/chat_storage.py`：本地会话持久化

技术设计见 [TECHNICAL_DESIGN.md](TECHNICAL_DESIGN.md)，产品说明见 [PRODUCT_DOC.md](PRODUCT_DOC.md)。

## 相关文档

- [README.md](README.md)：英文概览
- [TECHNICAL_DESIGN.md](TECHNICAL_DESIGN.md)：架构与运行设计
- [SKILL_SYSTEM.md](SKILL_SYSTEM.md)：技能与工具模型
- [PRODUCT_DOC.md](PRODUCT_DOC.md)：产品定位与用户流程
- [USER_GUIDE.md](USER_GUIDE.md)：图文使用指南
- [ROADMAP.md](ROADMAP.md)：项目状态

## 许可证

MIT License
