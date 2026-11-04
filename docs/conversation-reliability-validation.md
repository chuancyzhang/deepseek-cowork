# 对话可靠性修复验证（2026-11-04）

修复正文空白丢失与同源片段重复、重开回放偏移、Responses 完成正文覆盖、图片预算来源类型误判，并补齐图片权限指引和压缩终态诊断。没有数据库迁移、权限缓存、自动降级或提示词/工具定义调整；原账本和 replay 不重写。新保存的中断片段恢复原始空白，后续送模文本可能与旧错误行为不同。

## 验证结果

在 `main` 的 `bf2205c7` 基线上实施并验证，仅包含本次对话修复，不包含 LibreOffice 或其他分支改动。

242 项针对性测试通过（不把重复运行及子测试另算）：

| 范围 | 通过 | 主要证据 |
|---|---:|---|
| 历史投影、图片预算、Responses、检查点终态 | 105 | 原文区间、空锚点、多阶段和分页顺序；真实附件结构；缩短/替换/空正文失败；无自动权限重试，手动关闭选项后其他工具仍发送 |
| 长上下文存储与完整 Agent | 33 | 失败不提交窗口、既有显式重试、历史查询、附件和稳定前缀 |
| 模型配置流程 | 19 | 已保存分组限制直接显示，选项不会自动关闭，保存后图片理解保留 |
| 停止、引导、会话隔离及快照接口 | 14 | 缩进和换行保存、SQLite 重开、正常快照用途继续有效；等待真实收尾后，工具结果仍在补充消息之前、账本身份不重复 |
| 流事件、消息保存、历史分页及新增完整链路 | 40 | 同进程序号去重；已保存完整消息与旧片段的快照回放；快照/增量混合坐标；思考空锚点与待显示文本；恢复失败隔离待投递队列；真实 Provider→Worker→UI 冲突原文保存、未执行工具、手动重试 |
| Chat Completions 与 Anthropic 适配器 | 31 | 响应 ID 与事件类型保留；Anthropic 检查点缺少 message_stop 或成功 stop_reason 不视为完成，max_tokens 保留为输出上限原因 |

既有 `test_completed_run_does_not_reappend_tool_round_committed_before_guidance` 原先未提供源消息身份、未等待异步收尾，且把展示去重错误地等同于删除协议账本中的工具轮次。现按真实事件提供身份和工具结果，等待检查点完成，在独立 SQLite 目录分别检查账本和展示投影。该用例同时验证空白占位阶段不会把旧工具结果移动到补充消息之后。没有运行整个仓库测试集。

补充验收覆盖：已保存 `Hello Alice` 后回放同源快照不新增正文，下一源消息的相同文字仍正常显示；多次快照中间被替代的增量不重复、不挤占后续跳过位置；最后快照短于已保存覆盖范围时，后续增量继续按剩余覆盖量跳过；旧片段的边界空白在保存、重开及分页后保持一致。未增加持久化字段或产品状态。

## 复现命令

在仓库根目录使用 PowerShell，测试数据与临时文件写入 `.tmp/conversation-main/`；需要短路径的 Qt 用例使用 `.tmp/three-issues-investigation/` 下独立临时目录，不使用个人会话或真实模型：

```powershell
$taskTemp = Join-Path (Get-Location) '.tmp/conversation-main/verify'
New-Item -ItemType Directory -Force $taskTemp | Out-Null
$env:TEMP = $taskTemp
$env:TMP = $taskTemp
$env:APPDATA = $taskTemp
$env:LOCALAPPDATA = $taskTemp
$env:QT_QPA_PLATFORM = 'offscreen'
$env:COWORK_EXTERNAL_BACKGROUND_SERVICES = '0'
.venv/Scripts/python.exe -X utf8 -B -m pytest -p no:cacheprovider --basetemp "$taskTemp/pytest" -q test/test_conversation_render.py test/test_context_images.py test/test_deepseek_provider.py test/test_long_context_provider.py test/test_long_context.py test/test_long_context_agent.py test/test_model_configuration_flow.py test/test_runtime_stream.py test/test_message_persistence.py test/test_history_paging.py test/test_conversation_reliability.py
.venv/Scripts/python.exe -X utf8 -B -m pytest -p no:cacheprovider --basetemp "$taskTemp/ui" -q test/test_conversation_linear_interactions.py -k 'completed_run_does_not_reappend or stop_marks_partial or stop_persists_all_visible or content_snapshot or live_guidance or accepted_guidance or pending_guidance or background_session or prefix_and_journal or daemon_native_finish'
.venv/Scripts/python.exe -X utf8 -B -m pytest -p no:cacheprovider --basetemp "$taskTemp/protocol" -q test/test_chat_completions_stream.py test/test_anthropic_provider.py
```

实际验证按上述范围分组隔离运行，避免多进程共用测试配置目录。第三方库的弃用警告没有作为测试通过的证据。

## 界面与边界

真实 Qt 控件离屏渲染，检查 1280×900 浅色 100% 和 760×760 深色 150%，验证主题安全预览不落盘、图片选项未自动改变。渲染器使用 `.tmp/conversation-reliability/` 隔离配置；未发起网络调用。

```powershell
.venv/Scripts/python.exe -X utf8 -B scripts/render_conversation_reliability.py --output docs/screenshots/conversation-reliability
.venv/Scripts/python.exe -X utf8 -B scripts/render_conversation_reliability.py --output docs/screenshots/conversation-reliability --scale 1.5 --dark --narrow
```

- [正文冲突与重试](screenshots/conversation-reliability/final-conflict-light-100.png)
- [图片权限错误](screenshots/conversation-reliability/image-permission-light-100.png)
- [模型设置中的分组限制](screenshots/conversation-reliability/model-permission-light-100.png)
- [压缩失败与原因](screenshots/conversation-reliability/compression-failure-light-100.png)
- [深色小窗口 150%](screenshots/conversation-reliability/model-permission-dark-150-narrow.png)

未实测故障机器的真实服务、原始数据库和 Windows 原生 DPI；外机压缩失败的底层原因仍未确定。本轮没有调整压缩预算、提示词、算法或自动重试规则，也没有构建或发布安装包。
