---
name: favorites-manager
license: Apache-2.0
description: Create, inspect, update, schedule, launch, and remove saved favorite prompts and capability combinations. Use when the user asks to save a recurring way of working, configure a reusable prompt or skill bundle, choose chat versus a project workspace, add an optional schedule, run a favorite, or inspect its run history.
metadata:
  author: deepseek-cowork team
  version: "1.0.0"
allowed-tools: [list_favorites, upsert_favorite, configure_favorite_schedule, delete_favorite, launch_favorite, list_favorite_run_history]
---

# 常用库管理

用这些工具管理用户可复用的工作模式。常用项是主体，定时运行只是每个常用项上的可选附加能力。

## 操作顺序

1. 当前配置不明确时，先调用 `list_favorites`。
2. 创建或修改提示词、能力组合、执行位置时，调用 `upsert_favorite`。
3. 只调整附加计划时，调用 `configure_favorite_schedule`。
4. 用户要求开始工作时，调用 `launch_favorite`；不要自行复述提示词来模拟启动。
5. 删除前调用 `delete_favorite`，并遵循工具发起的确认流程。

## 字段规则

- `prompt` 与 `skill_names` 至少提供一个，可以分别保存，也可以组合保存。
- `execution_mode="chat"` 时不要填写或推断 `workspace_dir`；聊天不会携带项目工作区。
- `execution_mode="workspace"` 时必须使用已经存在的项目目录。
- 每个常用项最多一个 `schedule`。
- `schedule.delivery` 只能引用桌面端通过一次性绑定码创建的 `binding_id`；不要生成或猜测绑定 ID。用户尚未绑定时，引导其在常用编辑器中完成绑定。
- 没有常用提示词的能力组合若要定时运行，计划必须使用 `prompt_mode="custom"` 并提供 `custom_prompt`。
- 定时计划继承常用项的能力组合和执行位置，不单独存储 Agent、模型或附件。
- 新计划默认暂停；只有用户明确要求启用时才设 `enabled=true`。

## 安全边界

- 启动和删除会请求用户确认。
- 配置失败时直接说明具体无效字段；不要替换工作区、能力或计划参数。
- 计划依赖桌面应用运行；应用关闭期间错过的触发只记录为已错过，不补跑。
