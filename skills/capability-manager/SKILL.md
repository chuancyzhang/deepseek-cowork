---
name: capability-manager
description: 查找适合任务的技能、工具与连接器，解释缺少配置、未开启、加载失败或会话范围限制，并指向现有管理入口。
allowed-tools: [inspect_capabilities, tool_search]
---
# 能力发现与配置

先根据任务查当前工具，使用 tool_search 发现可执行能力。用户问“有没有”“为什么不能用”“在哪里配置”，用 inspect_capabilities 读取已有目录状态，不把状态查询当成执行授权。

- available：目录已就绪；外部服务实际连通性仍以调用结果为准。
- disabled：引导用户在返回的管理入口开启；不要重复安装。
- needs_configuration：说明缺少的配置字段，使用现有凭据界面，不索取聊天中的明文密钥。
- load_or_dependency_error：在技能或 MCP 管理页查看具体错误和修复入口；连接失败不能说成搜索无结果。
- out_of_scope：说明当前会话/Agent 范围限制，不借其他能力绕过。
- not_loaded：目录已经存在，但当前运行未加载；配置完成后重新查询并按现有运行快照规则继续。

仅当本地能力不能满足需求时，搜索已支持的 SkillHub 外部目录，阅读详情与来源。安装交给 skill_builder；启停、连接、配置、卸载使用现有管理页面。查询不会连接服务、安装依赖或暴露禁用工具的参数定义。不要假设不存在的连接器或目录，也不要自动给任务增加外部账号依赖。
