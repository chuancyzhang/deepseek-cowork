# DeepSeek Cowork 文档中心

当前应用版本：**5.0.8**

这里是 DeepSeek Cowork 的权威文档入口。项目以中文文档为主，根目录
`README.md` 提供英文概览。

## 从哪里开始

- [用户指南](user-guide.md)：安装、模型配置、项目、文件、Agent、Skill、交付物和常见问题
- [产品文档](product.md)：产品定位，以及 Everything is Tool、AI 设计 UI、经验系统三条核心理念
- [技术设计](technical-design.md)：从最小 Agent Loop 逐步推导当前底层实现
- [Skill 系统](skill-system.md)：Tool、Skill、MCP、经验与渐进披露
- [AI 主题与 Visualize](guides/ai-theme-and-visualize.md)：面向普通用户的专题操作指南
- [路线图](roadmap.md)：当前状态、近期重点与候选方向
- [发布记录](releases/index.md)：各版本发布说明

## 文档职责

| 文档 | 面向谁 | 只回答什么 |
|---|---|---|
| README | 第一次访问项目的人 | Cowork 是什么、如何安装、从哪里继续阅读 |
| 用户指南 | 使用桌面应用的人 | 一个操作如何完成、失败后如何恢复 |
| 产品文档 | 产品、设计和技术决策者 | 为什么这样设计、能力边界是什么 |
| 技术设计 | 开发者和希望理解 Agent 的用户 | Agent Loop 如何演进、各模块如何协作 |
| Skill 系统 | 能力作者和集成开发者 | Tool、Skill、MCP 和经验如何进入运行时 |
| 专题指南 | 需要深入使用特定能力的人 | 具体工作流、安全边界和常见问题 |
| 发布记录 | 升级和验收人员 | 某个版本当时改变了什么 |

## 内容来源

- 当前行为以 `core/`、`main.py`、`ui/`、内置能力清单和测试为准。
- 当前态文档统一使用 5.0.8；旧版本号只在历史发布记录中出现。
- Markdown 是权威内容源；Word 版本由 Markdown 生成。
- `skills/**/SKILL.md` 与 `ai_skills/**` 是运行时或上游能力内容，不属于本次文档重写范围。
- 旧根目录文档路径不再保留跳转页；所有权威入口以本页为准。
