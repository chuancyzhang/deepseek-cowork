# DeepSeek Cowork Skill 系统

当前应用版本：**5.0.8**

Skill 系统只解决一个问题：怎样让 Agent 在不扩大核心执行协议的前提下，
获得可发现、可组合、可积累的专业能力。

## 1. Tool 是执行面，Skill 是经验包

Cowork 保持单一执行面：

- **Tool**：模型可以直接调用的动作。
- **Skill**：围绕一类任务组织的指导、边界、经验、工具引用和资源。
- **MCP**：把外部服务暴露成 Tool 的标准连接方式。

模型不会“调用 Skill”。它读取 Skill 提供的上下文，然后调用 Tool。

```mermaid
flowchart LR
    Q["当前任务"] --> D["发现相关 Skill"]
    D --> G["披露指导与经验"]
    G --> T["选择 Tool"]
    T --> E["统一 Agent Loop 执行"]
```

这避免了 Skill、自动化、MCP 和内置能力各自形成不兼容的运行协议。

## 2. 一个 Skill 可以包含什么

```text
<skill>/
  SKILL.md
  skill.json
  impl.py
  experience/
    entries.jsonl
  references/
  scripts/
  assets/
```

- `SKILL.md`：面向 Agent 和人的权威指导。
- `skill.json`：发现、工作台、配置、Tool 和运行时元数据。
- `impl.py`：可选的 Python Tool 实现。
- `experience/entries.jsonl`：可选的结构化经验。
- `references/`：按需披露的长资料。
- `scripts/`：通过声明入口执行的脚本。
- `assets/`：Skill 使用的静态资源。

Cowork 也可以安装符合 Agent Skills 约定、根目录带 `SKILL.md` 的能力包。
安装时保留上游 `SKILL.md`，只生成 Cowork 所需的本地索引元数据。

## 3. 发现与渐进披露

如果所有 Skill、参考资料和 Tool Schema 都在每轮请求中注入，Prompt 会随
能力数量快速膨胀。Cowork 将披露分成四个实际层次：

1. **不披露**：任务无关。
2. **简要信息**：名称、用途、标签和推荐 Tool。
3. **完整指导**：当前任务明确命中 Skill。
4. **参考资料和经验条目**：只有确实需要时才展开。

`tool_search` 是延迟发现入口。它可以：

- 搜索尚未暴露的 Tool；
- 匹配相关 Skill；
- 返回推荐的执行入口；
- 让命中的 Tool Schema 在下一次模型请求中可见。

Skill 全文和自动命中的经验只参与当前运行，不写入正常会话历史。这样既能
复用能力，又能保持后续 Prompt 前缀稳定。

## 4. Skill 来源

Cowork 从以下来源构建能力目录：

- `skills/`：核心内置能力；
- `ai_skills/`：随包可选能力和用户能力；
- 标准 Agent Skill：由安装工具写入用户能力目录；
- MCP Server：作为合成 Tool Provider 出现在目录中。

能力目录由 `SkillCatalogService` 维护为进程级不可变快照。UI 和 daemon
分别持有自己的快照；请求 Worker 只创建轻量运行视图，不在提交消息时扫描
目录、导入实现或安装依赖。

## 5. Tool 注册

Tool 最终进入统一 `ToolRegistry`。记录包含：

- `name`、`description` 和输入 Schema；
- `read_only`；
- `destructive`；
- `requires_user_interaction`；
- 所属 Skill、工具类型和搜索提示；
- 延迟 Handler 或 MCP 映射。

声明式 Skill 可以在 `skill.json.tools` 中绑定 `impl.py:function`。Handler
直到首次调用才导入。旧版只有 `impl.py` 的 Skill 仍可通过反射注册公开函数。

脚本型 Skill 不要求模型猜目录和命令，而是声明 `script_entries`，统一通过
`run_skill_script` 在隔离运行时中执行。

## 6. 运行配置

`skill.json` 可以声明 `config_fields`：

- `text`：普通文本；
- `secret`：密钥；
- `select`：固定选项；
- `required`、`default`、`options`；
- `env`：执行时注入的环境变量；
- `help`、`placeholder`；
- `action_label`、`action_url`：不含凭据的 HTTPS 辅助入口。

配置保存在本地 `skill_configs`。Tool 或脚本执行时，只按字段声明注入环境。
必填项缺失会明确失败，不进行静默降级。

## 7. 托管 MCP

Skill 可以通过 `mcp_server_presets` 描述 `stdio` 或 Streamable HTTP Server。
保存 Skill 配置时，Cowork 会：

1. 解析 `{{ENV_NAME}}` 配置引用；
2. 生成或更新带 `source_skill` 的 MCP 条目；
3. 启用由该 Skill 管理的 Server；
4. 保留独立的连接测试和诊断入口。

`runtime: skill_python` 的 stdio Server 使用所属 Skill 的隔离 Python 环境。
托管认证只持久化配置引用，短期 access/refresh token 保留在内存中。

MCP 的远端工具仍会被映射为本地 Tool 名称，并通过统一 Agent Loop 调用。

## 8. 依赖准备

Python 和 Node 依赖不会在应用启动或 Skill 启用时全部安装。

`DependencyCoordinator` 在 Tool 首次调用前按“Skill + 依赖哈希”准备环境：

- 同一哈希的并发请求共享 single-flight；
- 默认超时 300 秒，可配置为 30–1800 秒；
- 成功和失败状态都会持久化；
- 失败不会在新会话中自动重试；
- 用户可从能力中心显式重试；
- 依赖声明变化会产生新的哈希。

## 9. 经验系统

经验由 Skill 承载，与指导、工具引用和资源一起形成可复用能力包。

结构化条目保存在：

```text
experience/entries.jsonl
```

典型字段包括经验正文、Tool 名、任务类型、错误模式、标签、来源和时间。

`update_experience` 在未指定 Skill 时记录到 `general-experience`；指定 Skill
时记录到相应能力。运行时同时维护简短经验摘要，用于搜索和低成本披露。

完整经验不会默认进入每次 Prompt。`disclosure_level_defaults` 控制是否在
完整 Skill Prompt 中包含条目；调用方还可以显式请求参考资料或经验。

### 会话沉淀

“沉淀为 Skill”采用提取、确认两阶段流程：

1. 选择来源会话范围。
2. 对密钥和本地路径进行脱敏。
3. 提取带消息 ID 引用的目标、模式、约束、失败、验证和资源候选。
4. 展示证据置信度与缺失项。
5. 用户选择创建、合并指导或追加经验。
6. 第二阶段只接收规范化证据和非敏感目标快照。
7. 通过 Schema、引用、敏感内容、路径、Python AST 和目标 revision 校验。
8. 用户最终确认后原子保存。

执行过的原始 Python 代码不会直接复制进 Skill。用户选择脚本候选时，编译器
会根据用途重新生成参数化实现，并经过同一静态质量门。

## 10. 变更与热更新

能力变更统一发布 `SkillChangeEvent`，包含事件 ID、动作、Skill 名称、来源、
会话和 revision。

```mermaid
flowchart LR
    M["创建/更新/启停/删除"] --> V["校验并原子发布目录"]
    V --> E["SkillChangeEvent"]
    E --> U["UI 重建快照"]
    E --> D["daemon 重建快照"]
    U --> B["下一模型请求边界切换"]
    D --> B
```

重复事件按 ID 幂等，旧 revision 不覆盖新快照。已经运行的 Tool 不会被中断；
Worker 只在下一次模型请求边界应用最新快照。文件监听只负责修复外部编辑，
不参与业务强一致链路。

## 11. 导入、导出与兼容

能力中心支持：

- 导入单个 Skill 文件夹；
- 导入包含多个 Skill 的目录；
- 导入 ZIP；
- 安装标准 Agent Skill；
- 导出单个或多个 Skill；
- 校验文件并调试 Tool、脚本和 MCP。

ZIP 先解压到临时目录并检查路径穿越。最终名称来自元数据；已有目标不会被
静默覆盖。

只有 `impl.py` 的旧 Skill 继续受支持，系统会生成最小发现记录。空目录、
缓存目录和已经删除实现留下的残片不会重新出现在能力中心。

## 12. 设计原则

- Tool 保持直接、统一、可观察。
- Skill 聚合任务指导、经验和资源。
- 经验按需披露，不让 Prompt 无限增长。
- 重依赖能力保持可选和延迟准备。
- 配置缺失、连接失败和依赖错误必须暴露根因。
- 用户明确控制哪些对话内容进入长期可复用能力。
