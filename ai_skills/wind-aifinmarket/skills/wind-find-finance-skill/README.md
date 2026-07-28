# wind-find-finance-skill

> **Wind 金融能力入口（meta-skill）** · 读 skill 清单，帮 AI 列举平台能力并推荐安装

---

## 这是什么

不是数据 skill，是**入口 skill**：

- 用户问"有什么金融能力" / 提了金融问题但 AI 不确定用哪个 → 触发本 skill
- AI 读 `references/skills-catalog.md` → 列举平台所有可用 skill（数据发现 + 金融分析两类）
- 给出对应安装命令，让用户自助挑装
- Cowork 使用固定快照，不提供运行时在线更新。

---

## 安装

> Cowork 适配：所需子 Skill 已随 `wind-aifinmarket` 固定快照内置，不执行安装或在线更新命令。

> 想限制在当前项目内用，把命令的 `-g` 去掉即可。`-g` 会自动 symlink 到机器上所有已识别的 AI agent（Claude Code / Cursor / OpenClaw / Hermes 等）。

**不需要 API Key** —— 本 skill 不调任何 MCP server，纯读文档。

---

## 目录结构

```
wind-find-finance-skill/
├── SKILL.md                         # AI 加载的核心守则（5 步触发流程）
├── references/
│   └── skills-catalog.md            # 平台 skill 清单本地副本
├── scripts/
└── README.md
```

**没有数据 cli.mjs**——Cowork 通过根插件的只读检索和加载 Tool 处理。

---

## 工作原理

本 skill 是 **meta-skill**，跟数据 skill 的区别：

| 维度                       | 数据 skill（如 wind-mcp-skill） | 本 skill                       |
| -------------------------- | ------------------------------- | ------------------------------ |
| 调底层 MCP server          | ✅                              | ❌                             |
| 需要 WIND_API_KEY          | ✅                              | ❌                             |
| 返回业务数据               | ✅                              | ❌                             |
| 返回 skill 推荐 + 安装命令 | ❌                              | ✅                             |
| 谁来调用                   | AI 直接调（取数据答用户）       | AI 在不确定用哪个 skill 时先调 |

AI 加载 SKILL.md 后按守则操作：

1. Read `references/skills-catalog.md` → 拿本地清单
2. 按用户问题筛 1-3 个相关 skill 列出（含安装命令）
3. 通过根插件加载所选子 Skill；固定快照的升级由 Cowork 发布流程完成。

---

## 升级

> Cowork 适配：所需子 Skill 已随 `wind-aifinmarket` 固定快照内置，不执行安装或在线更新命令。

`references/skills-catalog.md` 随 skill 包一起更新。

---

## 设计要点

- **轻代码**：核心推荐仍是 markdown + AI 工具能力；Node.js 脚本只做更新提醒
- **跨 agent 通用**：只要 agent 让 LLM 能 Read 文件 + WebFetch URL 即可
- 仅写当前 skill 的 `scripts/update-state.json` 与临时锁文件，不写业务数据
- **平台版本号** 由我们维护，跟 skill 自身 frontmatter version 解耦；改了 monorepo 哪个 skill 就把 skill.md 那一行 +1

---
