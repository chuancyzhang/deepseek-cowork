# DeepSeek Cowork

[中文](README_CN.md) | [English](README.md) | [文档中心](docs/index.md)

DeepSeek Cowork 是一个面向 Windows 的本地桌面 Agent 工作台。它把对话、项目文件、
Tool 执行、能力扩展、常用工作模式和交付物组织成一条可观察、可干预、可恢复的工作流。

本项目为个人探索，与 DeepSeek 官方无隶属关系。

当前应用版本：**5.1.0** · [查看本版发布说明](docs/releases/5.1.0.md)

## 5.1.0 的重点

- **结果可以直接修订**：在“文件与交付物”中预览常见格式，并安全编辑 DOCX、HTML、XLSX、CSV/TSV、Markdown、JSON、XML、YAML 与普通文本。
- **能力按任务组织**：能力商城用“查找资料、处理文档、分析数据、制作内容、金融研究”组织入口；新对话首页直达 PPT、金融、数据和浏览器任务。
- **常用连接更容易启用**：常用任务集中展示计划与运行历史，并可把定时结果发送到已配置的企业消息会话；企业消息支持飞书、钉钉、企业微信、QQ 和微信，并保持单活动渠道。
- **Agent 工具契约更稳定**：核心 Tool 首轮直接可见，普通文本统一使用完整读取与安全补丁提交；DeepSeek Responses 能保留推理、函数调用和服务端搜索顺序。这也是为 DeepSeek V4 Flash 正式版及后续 V4 Pro 正式版的后训练调用偏好和 Responses 协议兼容做准备。

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
