# DeepSeek Cowork

[中文](README_CN.md) | [English](README.md) | [用户手册](docs/user-guide.md) | [企业版介绍](docs/enterprise/README.md) | [技术文档](docs/technical-design.md)

DeepSeek Cowork 是面向 Windows 的本地桌面 Agent 工作台，把对话提问、项目文件读写、代码与工具执行、专业技能和成果编辑放在同一个窗口里。你只需说明目标、提供资料，就可以边看执行过程边在右侧工作区检查、修改并保存生成的文件。

本项目为个人独立开发项目，与 DeepSeek 官方无隶属关系。

文档适用版本：**5.4.3** · [本版说明](docs/releases/5.4.3.md) · [发布记录](docs/releases/index.md)

## 核心能力

- **对话与多任务协作**：支持绑定本机项目文件夹或使用独立聊天目录；任务运行中可随时补充要求、查看代码与命令的实时输出，也可将子任务拆分到独立视图中跟进和控制。
- **文件阅读、编辑与变更追溯**：右侧工作区支持 Word/PPT/PDF 连续阅读与 AI 版式检查、Excel 表格编辑与宽表区域阅读、代码高亮编辑、HTML 页面内修改及 Markdown 写作；既能按轮次查看本轮文件改动，也能对比 Git 工作目录实时差异。
- **数据分析、图表与多媒体**：随包内置 Python 数据分析与 Notebook 执行环境，支持对话内 Mermaid 流程图、Visualize 交互图表、HTML/JavaScript 沙箱预览，以及单独配置的图片生成/编辑与视频生成/理解。
- **资料库与能力扩展**：集中管理本地公共资料与项目资料，支持连接 WeKnora、腾讯文档、腾讯乐享；内置常用办公与分析技能，支持从 SkillHub 或本地包安装扩展技能、接入 MCP 服务、切换专家角色，以及使用内置网页或外部 Chrome / Edge 完成浏览器自动化。
- **定时任务与企业协作**：支持保存常用任务、设置定时计划，并通过飞书、钉钉、企业微信、QQ 或微信收发任务与结果；企业版提供部门层级授权、统一分发模型与 Skill/MCP/专家、团队空间协作及聊天请求合规审计。
- **个性化与经验积累**：支持多套主题与阅读排版、创造模式（让 AI 设计界面并在隔离预览中试跑确认）、[UI SDK](desktop/sdk/README.md) 插件扩展，以及跨轮次保留长期记忆与可复用经验。

## 开始使用

1. 下载并完整解压 `cowork-desktop-v版本号-windows-x64.zip`，在解压后的 `Cowork` 目录中运行 `deepseek-cowork.exe`。
2. 打开“设置 → 模型与服务”配置 API Key 或账号模型；企业用户可在“设置 → 企业账号”登录并使用管理员分配的资源。升级安装会自动沿用本机原有的配置、聊天记录、附件和工作区，无需手动导入。
3. 点击“新建聊天”。处理固定项目时先在输入框下方选择项目文件夹，临时任务可直接开始；拖入参考文件并描述你希望得到的成果。
4. 在对话中跟进执行进度或随时补充要求，完成后点击文件名即可在右侧工作区查看、编辑并保存结果。

日常操作与常见问题详见[用户手册](docs/user-guide.md)。

## 从源码运行

桌面端采用 **Electron + React / TypeScript + Python + SQLite** 架构：Electron 负责桌面窗口与原生网页视图，React 承载工作台与插件界面，Python 负责模型调度与工具执行，SQLite 保存本机历史。界面说明见[认识工作台](docs/user-guide.md#认识工作台)，架构与构建规范见[技术文档](docs/technical-design.md#构建与发布)。

准备 Python 3.12+（Windows 发行包使用 3.14）以及包含 npm 的 Node.js 22.12+，在仓库根目录依次执行：

```powershell
git clone https://github.com/chuancyzhang/deepseek-cowork.git
cd deepseek-cowork
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm --prefix desktop ci
npm --prefix web/editors ci
node desktop/node_modules/electron/install.js
.\.venv\Scripts\python.exe main.py
```

## 构建发行包

在 Windows 构建环境中拉取固定版本的随包运行时，再构建桌面可执行程序与发行压缩包：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\fetch_runtimes.ps1
$env:COWORK_PYTHON = (Resolve-Path .venv/Scripts/python.exe).Path
npm --prefix desktop run package:win
```

构建产物位于 `desktop/out/`，包含完整运行目录、发行包 `cowork-desktop-v版本号-windows-x64.zip` 与更新清单 `latest-desktop-windows-x64.json`。本地打包不会自动上传到 [GitHub Releases](https://github.com/chuancyzhang/deepseek-cowork/releases)，代码签名与公开发布独立执行。Apple Silicon Mac 与统信 UOS V25 需在对应目标设备上单独完成发行构建与实机验证。详见[技术文档](docs/technical-design.md#构建与发布)。

## 设计理念

- **Everything is Tool**：所有可执行动作统一走 Tool 注册、权限审批、执行记录与结果回传；Skill 组织工作方法与参考资源，专家组合角色提示词与可用能力。
- **AI 可设计界面**：无论是调整主题还是通过创造模式与 [UI SDK](desktop/sdk/README.md) 定制工作台，都会先在示例环境中隔离试跑，确认无误后再应用，后台任务与未保存草稿不受影响。
- **经验系统而非微调**：完整历史保留真实过程，长期记忆沉淀跨会话事实，经验记录已验证的可复用做法，Skill 固化成套专业流程。

## 文档导航

| 你想了解什么 | 推荐入口 |
| --- | --- |
| 日常任务操作、功能配置与问题排查 | [用户手册](docs/user-guide.md) |
| 企业版角色分工、权限分配、审计与客户端更新 | [企业版介绍](docs/enterprise/README.md) |
| 部署运维与管理企业后台 | [部署指南](docs/enterprise/deployment.md) · [管理员指南](docs/enterprise/admin-guide.md) |
| 系统架构、Skill / 插件开发、测试与打包发布 | [技术文档](docs/technical-design.md) · [UI SDK](desktop/sdk/README.md) |
| 历次版本功能演进 | [发布记录](docs/releases/index.md) |

## 许可证

Apache-2.0
