# DeepSeek Cowork 文档中心

当前应用版本：**5.2.5**

这里集中维护 DeepSeek Cowork 的当前态文档。第一次使用先读用户指南；准备升级
先读发布说明；理解实现或扩展能力时再进入技术文档。

## 按目标选择文档

| 你的目标 | 从这里开始 | 继续阅读 |
| --- | --- | --- |
| 安装并完成第一次任务 | [用户指南](user-guide.md) | [5.2.5 发布说明](releases/5.2.5.md) |
| 管理能力、项目和设置 | [设置与能力管理](unified-management.md) | [执行授权](GOD_MODE.md) |
| 使用资料与保存成果 | [资料库](knowledge_library.md) | [账号与连接](account-connections.md) |
| 登录外部服务或恢复认证 | [账号与连接](account-connections.md) | [用户指南](user-guide.md) |
| 判断产品适合什么工作 | [产品文档](product.md) | [路线图](roadmap.md) |
| 理解 Agent 如何执行与恢复 | [技术设计](technical-design.md) | [Skill 系统](skill-system.md) |
| 创建、安装或调试能力 | [Skill 系统](skill-system.md) | [技术设计](technical-design.md) |
| 自定义界面或使用交互可视化 | [AI 主题与 Visualize](guides/ai-theme-and-visualize.md) | [产品文档](product.md) |
| 按需减少模型请求中的敏感信息 | [可选数据安全插件](data-security.md) | [源码验证记录](data-security-validation.md) |
| 准备升级或验收发行包 | [发布记录](releases/index.md) | [用户指南](user-guide.md) |

## 推荐阅读顺序

1. [用户指南](user-guide.md)：模型、工作区、任务、文件、交付物和排错。
2. [产品文档](product.md)：产品目标、三条核心理念和明确边界。
3. [技术设计](technical-design.md)：Agent Loop、Tool、安全、模型协议、持久化与桌面运行时。
4. [Skill 系统](skill-system.md)：能力来源、发现、配置、MCP、依赖、经验和变更发布。
5. [路线图](roadmap.md)：发布后的近期重点与候选方向。

## 文档职责

| 文档 | 面向谁 | 维护什么 | 不维护什么 |
| --- | --- | --- | --- |
| README | 第一次访问项目的人 | 一句话定位、当前重点、安装和导航 | 逐项配置和内部实现 |
| 用户指南 | 桌面应用用户 | 完成任务的操作路径、状态与恢复方式 | 源码类名和协议细节 |
| 产品文档 | 产品、设计与技术决策者 | 价值、原则、产品结构和边界 | 提交历史和操作步骤 |
| 技术设计 | 开发者 | 当前运行模型、关键不变量和源码入口 | 用户教程和未来承诺 |
| Skill 系统 | 能力作者与集成开发者 | Skill 生命周期、Tool/MCP 契约和经验 | 单个插件的完整手册 |
| 路线图 | 维护者与贡献者 | 当前阶段、近期重点和候选方向 | 已发布版本的流水账 |
| 发布记录 | 升级与验收人员 | 某个版本交付了什么、边界和验收项 | 对历史版本的反向改写 |

## 内容基线

- 当前行为以 `core/`、`main.py`、`ui/`、内置能力清单和测试为准。
- 当前态文档以 `core/app_version.py` 的 5.2.5 为源码基线；历史版本可在发布记录、兼容说明和带日期的验证记录中引用，不代表当前版本已完成全部验收。
- Markdown 是权威内容源；已删除的派生 Word 文档不属于当前文档基线。
- `skills/**/SKILL.md` 与 `ai_skills/**` 是运行时或上游能力内容，不与产品文档混写。
- 发布变化先进入对应版本的发布说明；只有仍然成立的行为才进入当前态文档。
- 截图以[用户指南](user-guide.md)引用的 `docs/guides/user-guide/` 为基准，同场景直接复用；独有专题图按需保留。旧 UI 验证脚本默认输出到 `.tmp/documentation-qa/`，不自动覆盖文档截图。
- 文档校验使用 `.venv/Scripts/python.exe scripts/check_docs.py` 与 `.venv/Scripts/python.exe -m pytest test/test_documentation.py -q`；检查版本声明、链接、锚点和截图场景，不固定截图张数。

本次同步范围、截图清单和验证边界见[5.2.5 文档维护记录](documentation-update-5.2.5.md)。
