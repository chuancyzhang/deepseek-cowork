# DeepSeek Cowork

[中文](README_CN.md) | [English](README.md) | [文档中心](docs/index.md)

DeepSeek Cowork 是一个 Windows 桌面 Agent 工作台。它把对话、项目文件、
Tool 调用、Skill、自动化、长期信息和交付物预览/编辑组织成一条可观察、可干预、
可恢复的工作流。

本项目为个人探索，与 DeepSeek 官方无隶属关系。

当前应用版本：**5.1.0**

## 三条核心理念

### Everything is Tool

所有模型可执行动作都通过统一 Tool 接口进入 Agent Loop。文件操作、命令、
MCP、用户交互、主题配置和外部服务共享同一套 Schema、权限、观测和结果
回填机制。

Skill 是经验与工作流指导，Agent 是角色和上下文，自动化是触发方式；它们
不会建立第二套执行协议。

### AI 设计 UI

Cowork 允许 AI 设计界面，但不允许 AI 接管应用代码。AI 可以配置主题令牌、
工作区场景、区域材质、组件样式、受控布局、图标和白名单文案。

主题必须通过 Schema 校验、revision 隔离预览和用户确认。关键控件、区域
归属、交互动作和安全恢复路径由代码保护。

### 经验系统

任务完成后，Agent 可以把工具技巧、失败模式和恢复方法记录为结构化经验。
经验归入特定 Skill 或 `general-experience`，在相关任务中按需披露。

经验是本地、显式、可查看、可编辑的运行知识层，不涉及模型权重调整。历史
负责追溯，记忆保存长期事实和偏好，经验改善操作方法，Skill 组织可复用能力。

详细说明见[产品文档](docs/product.md)。

## 主要能力

- 在独立聊天目录或明确项目工作区内运行本地 Agent。
- 在同一轮中交错展示 reasoning、Tool、阶段回复和最终回答。
- 通过 `@智能体` 召唤一个或多个已配置 Agent，并在聊天区分别实时查看各自的思考、Tool、阶段输出和最终回答；重新打开历史会话后可恢复完整过程。
- 运行中补充要求、停止任务、响应确认并查看结构化观测。
- 预览 Markdown、HTML、图片、PDF、DOCX、PPTX 和 XLSX，并在应用内安全编辑 DOCX、HTML、XLSX、CSV/TSV 与常见文本文件。
- 生成办公工作稿，并从 HTML 继续生成 PPTX、DOCX 或 PDF。
- 使用内置、随包、用户和标准 Agent Skill 扩展能力。
- 通过 `stdio` 或 Streamable HTTP 接入 MCP Tool。
- 管理模型、Agent、自动化、记忆、主题、企业消息和可选组件；企业消息支持飞书、钉钉、企业微信、QQ 与微信，并保持同一时间只运行一个渠道。
- 从新会话首页快速进入 PPT Agent、万得与东方财富金融分析、工作区数据/机器学习分析和浏览器自动化，并继续使用 Visualize、网页搜索及文档/数据工具包。

## 安装

### Windows 发行包

1. 从 [GitHub Releases](https://github.com/chuancyzhang/deepseek-cowork/releases) 下载最新版。
2. 解压 ZIP。
3. 运行 `deepseek-cowork.exe`。

### 运行环境与空间

- 基准运行环境为 4 核 CPU、8 GB 内存和 SSD；推荐 16 GB 内存，不要求独立显卡。
- 编辑器仅在首次进入编辑时按格式加载。未使用编辑功能时，不初始化 Canvas Editor、Univer 或 Office 文档模型。
- 离线编辑器资源实测约增加 2.99 MiB ZIP 体积、12.14 MiB 解压体积。以 366.3 MiB ZIP、820.8 MiB 解压目录为基线，整包预计约为 369.3 MiB / 832.9 MiB；最终数字以当次发布审计为准。
- 发布门禁限制编辑能力最多增加 10 MiB ZIP、30 MiB 解压体积，并禁止携带 `node_modules`、源码映射、CDN 或 Node 运行依赖。

### 从源码运行

前置要求：Python 3.10+

```bash
git clone https://github.com/chuancyzhang/deepseek-cowork.git
cd deepseek-cowork
python -m pip install -r requirements.txt
python main.py
```

### 构建发行包

先准备固定运行时：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\fetch_runtimes.ps1
```

再执行干净构建和打包审计：

```powershell
.\.venv\Scripts\python -m PyInstaller deepseek-cowork.spec --noconfirm --clean
.\.venv\Scripts\python.exe .\scripts\package_release.py
```

## 快速开始

1. 打开“设置 → 模型与服务”，配置模型并测试连接。
2. 新建独立聊天，或从项目中创建绑定工作区的会话。
3. 描述目标；需要资料时可通过输入区 `+` 添加文件，也可从文件管理器复制文件后直接粘贴。
4. 在消息流和任务观测中查看执行；遇到确认时检查范围再提交。
5. 从右侧“文件与交付物”预览或编辑结果，并按需继续转换。
6. 把长期偏好写入记忆，把可靠方法记录为经验或沉淀为 Skill。

## 文档

- [用户指南](docs/user-guide.md)
- [产品文档](docs/product.md)
- [技术设计：从 Agent Loop 到桌面运行时](docs/technical-design.md)
- [Skill 系统](docs/skill-system.md)
- [AI 主题与 Visualize](docs/guides/ai-theme-and-visualize.md)
- [路线图](docs/roadmap.md)

## 许可证

MIT License
