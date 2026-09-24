# DeepSeek Cowork

[中文](README_CN.md) | [English](README.md) | [文档中心](docs/index.md) | [设置与能力管理](docs/unified-management.md)

DeepSeek Cowork 是一个面向 Windows 的本地桌面 Agent 工作台。它把对话、项目文件、
Tool 执行、能力扩展、常用工作模式和交付物组织成一条可观察、可干预、可恢复的工作流。

本项目为个人探索，与 DeepSeek 官方无隶属关系。

当前应用版本：**5.2.5** · [查看本版发布说明](docs/releases/5.2.5.md)

## 当前版本的重点

- **多来源资料库**：连接 WeKnora、腾讯文档和乐享，读取选定资料并保存成果；来源、账号和任务范围独立绑定。
- **可选账号与连接**：集中管理登录、服务连接与能力授权；未选用统一连接时继续使用原配置。
- **可选数据安全**：插件及各项功能默认关闭，按需检查能力风险、提醒疑似凭证或令牌化模型请求中的选定文本。
- **任务控制与恢复**：停止时向受支持工具传递取消，保留已有输出；资料上下文按需加入，历史打开不产生额外提问。

## 核心工作流

1. 选择模型，创建独立聊天或绑定明确的项目工作区。
2. 描述目标，按需添加或粘贴文件、图片和参考资料。
3. Agent 在同一消息流中展示 reasoning、Tool、阶段结果与最终回答；运行中可以补充要求或停止。
4. 从右侧抽屉检查任务观测、文件和交付物，并继续编辑或转换结果。
5. 把稳定偏好写入记忆，把经过验证的方法沉淀为经验或 Skill；经常使用的工作模式保存为常用，并按需附加定时计划。

## 能力地图

| 层次 | 主要能力 |
| --- | --- |
| 工作区 | 独立聊天目录、项目边界、历史恢复、后台运行与分组分页 |
| 文件与交付物 | 文件/图片粘贴，Markdown、HTML、图片、PDF、DOCX、PPTX、XLSX 预览，安全编辑与 Office 转换 |
| Agent 运行时 | 流式 reasoning 与 Tool、运行中引导、结构化观测、子 Agent、daemon 与常用计划 |
| 能力扩展 | 内置/可选/用户 Skill，`stdio` 与 Streamable HTTP MCP，按需依赖和远程 Skill 安装 |
| 外部连接 | 浏览器自动化、网页搜索、金融与数据能力、飞书/钉钉/企业微信/QQ/微信 |
| 个性化 | 全局与工作区记忆、经验系统、`.cowork-theme` 安全主题、Visualize |

## 三条设计原则

- **Everything is Tool**：所有可执行动作共享 Tool Schema、权限、观测和结果协议；Skill 提供指导，Agent 提供角色，常用项上的可选计划提供触发方式。
- **AI 设计 UI**：AI 可以配置主题令牌、工作区场景和受控组件，但不能改写组件树、关键动作或安全恢复路径。主题必须经过校验、隔离预览和用户确认。
- **经验系统**：历史负责追溯，记忆保存长期事实，经验改善方法，Skill 组织可复用能力；这不是模型微调。

完整说明见[产品文档](docs/product.md)。

## 安装

### Windows 发行包

1. 从 [GitHub Releases](https://github.com/chuancyzhang/deepseek-cowork/releases) 下载最新版 ZIP。
2. 完整解压后运行 `deepseek-cowork.exe`；不要直接在压缩包内启动。
3. 首次启动时可直接填写 DeepSeek API Key；应用会验证官方接口、同步模型并优先选择推荐的 `deepseek-flash`。也可以稍后到“设置 → 模型与服务”手动配置。

基准环境为 4 核 CPU、8 GB 内存和 SSD；推荐 16 GB 内存，不要求独立显卡。

### 从源码运行

前置要求：Python 3.10+

```bash
git clone https://github.com/chuancyzhang/deepseek-cowork.git
cd deepseek-cowork
python -m pip install -r requirements.txt
python main.py
```

完整账号认证功能需额外安装 `requirements-connections.txt`，源码与发行包步骤见[账号与连接](docs/account-connections.md#源码与发行包)。

### 构建发行包

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\fetch_runtimes.ps1
.\.venv\Scripts\python -m PyInstaller deepseek-cowork.spec --noconfirm --clean
.\.venv\Scripts\python.exe .\scripts\package_release.py
```

发行包使用固定运行时、离线编辑器资源和打包审计；最终体积与组件清单以本次构建生成的报告为准。

## 文档

- [设置与能力管理](docs/unified-management.md)：能力、项目与设置的入口和保存边界
- [资料库](docs/knowledge_library.md)：多来源连接、引用、上传和失败恢复
- [账号与连接](docs/account-connections.md)：登录、授权、连接绑定和管理员分发
- [数据安全](docs/data-security.md)：默认关闭的检查与请求文本处理
- [执行授权](docs/GOD_MODE.md)：上帝模式、按次许可和停止边界
- [用户指南](docs/user-guide.md)：安装、配置与完整任务操作
- [产品文档](docs/product.md)：产品目标、核心理念与边界
- [技术设计](docs/technical-design.md)：Agent Loop、Tool、安全、持久化与桌面运行时
- [Skill 系统](docs/skill-system.md)：能力来源、发现、配置、MCP、依赖与经验
- [AI 主题与 Visualize](docs/guides/ai-theme-and-visualize.md)：普通用户专题指南
- [路线图](docs/roadmap.md)：当前阶段和候选方向
- [发布记录](docs/releases/index.md)：版本变化与验收重点

### 资料库

连接 WeKnora、腾讯文档或乐享知识库，在 Cowork 中浏览和阅读选定资料，并把本地产物保存回指定来源；搜索范围以各来源支持的能力为准。详见 [资料库使用说明](docs/knowledge_library.md)。

### 上帝模式与按次授权

上帝模式提供全局持续执行授权，仅影响新启动任务。关闭后，可信读取和本次任务产物加工可继续进行；修改原有资产或执行代码时申请本次许可。详见 [使用说明与产品验收清单](docs/GOD_MODE.md)。

## 许可证

Apache-2.0
