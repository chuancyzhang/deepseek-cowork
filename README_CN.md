# DeepSeek Cowork

[中文](README_CN.md) | [English](README.md) | [文档中心](docs/index.md)

DeepSeek Cowork 是一个面向 Windows 的本地桌面 Agent 工作台。它把对话、项目文件、
Tool 执行、能力扩展、常用工作模式和交付物组织成一条可观察、可干预、可恢复的工作流。

本项目为个人探索，与 DeepSeek 官方无隶属关系。

当前应用版本：**5.2.0** · [查看本版发布说明](docs/releases/5.2.0.md)

## 5.2.0 的重点

- **资料参与 Agent 工作**：新增基于 WeKnora 的资料库，支持浏览、搜索和阅读文档与 Wiki，将选定资料加入对话或项目。
- **成果保存回资料库**：将本地产物、对话文件和项目交付物保存到指定资料库与文件夹，支持多文件上传、文件预览和处理状态查看。
- **SkillHub 扩展专业能力**：在 AI 能力商城中浏览、搜索、安装和更新技能，安装后由用户配置和开启。

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
3. 首次启动时可直接填写 DeepSeek API Key；应用会验证官方接口、同步模型并优先选择推荐的 `deepseek-v4-flash`。也可以稍后到“设置 → 模型与服务”手动配置。

基准环境为 4 核 CPU、8 GB 内存和 SSD；推荐 16 GB 内存，不要求独立显卡。

### 从源码运行

前置要求：Python 3.10+

```bash
git clone https://github.com/chuancyzhang/deepseek-cowork.git
cd deepseek-cowork
python -m pip install -r requirements.txt
python main.py
```

### 构建发行包

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\fetch_runtimes.ps1
.\.venv\Scripts\python -m PyInstaller deepseek-cowork.spec --noconfirm --clean
.\.venv\Scripts\python.exe .\scripts\package_release.py
```

发行包使用固定运行时、离线编辑器资源和打包审计；最终体积与组件清单以本次构建生成的报告为准。

## 文档

- [用户指南](docs/user-guide.md)：安装、配置与完整任务操作
- [产品文档](docs/product.md)：产品目标、核心理念与边界
- [技术设计](docs/technical-design.md)：Agent Loop、Tool、安全、持久化与桌面运行时
- [Skill 系统](docs/skill-system.md)：能力来源、发现、配置、MCP、依赖与经验
- [AI 主题与 Visualize](docs/guides/ai-theme-and-visualize.md)：普通用户专题指南
- [路线图](docs/roadmap.md)：当前阶段和候选方向
- [发布记录](docs/releases/index.md)：版本变化与验收重点

## 许可证

MIT License


### 资料库

连接本机或远程 WeKnora，在 Cowork 中浏览、搜索和阅读资料，将资料带入 Agent 对话，并把本地产物保存回资料库。详见 [资料库使用说明](docs/knowledge_library.md)。
