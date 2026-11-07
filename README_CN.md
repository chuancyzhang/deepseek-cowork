# DeepSeek Cowork

[中文](README_CN.md) | [English](README.md) | [用户手册](docs/user-guide.md) | [技术文档](docs/technical-design.md)

DeepSeek Cowork 是面向 Windows 的本地桌面 Agent 工作台，将对话、项目文件、工具执行、专业能力和交付物放在同一工作流程中。你可以描述目标、提供资料、查看执行过程，并继续编辑生成的文件。

本项目为个人探索，与 DeepSeek 官方无隶属关系。

当前分支正在适配 **Apple Silicon Mac 与统信 V25 x86_64 个人桌面端**，保留 Windows 原有功能。新平台代码与构建入口已加入，尚未完成目标桌面实机验收，也未发布新平台安装包。功能边界见[用户手册](docs/user-guide.md#mac-与统信的功能范围)，架构和构建方法见[技术文档](docs/technical-design.md#平台适配边界)。

文档适用版本：**5.3.3** · [本版说明](docs/releases/5.3.3.md) · [发布记录](docs/releases/index.md)

## 可以完成什么

- **对话与项目工作**：独立聊天目录、项目会话、历史搜索、运行中补充要求、执行详情与子任务。
- **文件与交付物**：文件和图片附件、办公文档预览、文本与表格编辑、HTML 工作稿、Markdown 写作页及 Office/PDF 输出。
- **专业能力**：内置与可选 Skill、SkillHub 安装、MCP 服务、会话专家、浏览器自动化和语音转文字。
- **资料与协作**：WeKnora、腾讯文档、乐享资料库，以及企业账号、企业模型和团队空间。
- **重复任务**：常用任务、定时计划、消息渠道与计划结果投递。
- **个性化**：主题、AI 主题设计、Visualize、长期记忆与经验系统。

5.3.3 增加工作区展开与 Markdown 写作页、sub2api / New API 模型服务接入，并改进问卷、文件链接、预览与更新提示。

## 开始使用

1. 从 [GitHub Releases](https://github.com/chuancyzhang/deepseek-cowork/releases) 下载 Windows ZIP，完整解压后运行 `deepseek-cowork.exe`。
2. 在首次启动向导填写 DeepSeek API Key，或到“设置 → 模型与服务”添加服务和模型。也可使用企业提供的客户端登录企业账号。
3. 新建聊天，添加资料并描述任务；处理固定项目时先选择项目目录。
4. 查看回答与执行过程，在右侧工作区打开、编辑和保存成果。

详细操作见[用户手册](docs/user-guide.md)。

## 从源码运行

使用 Python 3.10+，在仓库根目录执行：

```powershell
git clone https://github.com/chuancyzhang/deepseek-cowork.git
cd deepseek-cowork
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

## 构建发行包

在 Windows 构建环境中准备固定运行时，再生成 EXE 和发行 ZIP：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\fetch_runtimes.ps1
.\.venv\Scripts\python.exe -m PyInstaller deepseek-cowork.spec --noconfirm --clean
.\.venv\Scripts\python.exe scripts/package_release.py
```

打包生成版本 ZIP、审计报告和 `latest.json`。更新说明读取 `docs/releases/版本号.md`；具体格式和发布步骤见[技术文档](docs/technical-design.md#构建与发布)。

Mac／统信必须在目标系统及 CPU 上构建，使用独立的 `deepseek-cowork-posix.spec` 与 `scripts/posix_release.py`；Windows 命令保持不变。新平台暂不适配企业专属客户端与企业分发服务。

## 设计思路

- **Everything is Tool**：可执行动作统一通过 Tool 注册、授权、执行和返回结果；Skill 组织指导与资源，专家提供角色。
- **AI 设计 UI**：用声明式主题与隔离预览调整工作界面，由用户确认应用。
- **经验系统**：历史保留过程，记忆记录长期事实，经验沉淀可复用方法，Skill 组织专业流程；这不是模型微调。

## 文档

| 目标 | 入口 |
| --- | --- |
| 安装后完成任务、使用设置和各项能力 | [用户手册](docs/user-guide.md) |
| 理解架构、开发能力、运行测试和构建发布 | [技术文档](docs/technical-design.md) |
| 部署与管理企业中心 | [企业平台说明](web/platform/README.md) |
| 查看已有版本变化 | [发布记录](docs/releases/index.md) |

## 许可证

Apache-2.0
