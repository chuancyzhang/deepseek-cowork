# 会话专家与 HTML 创作验收

2026-10-15，基线 `37e03b1`。使用说明见[用户指南](../../user-guide.md#选择专家与-html-创作)。

## 已验证行为

- 内置与自建专家共用搜索、选择、会话保存与主对话执行链路；选择后持续生效，关闭后恢复普通助手。
- 本轮提交冻结角色与名称，后续切换、改名、失败重试和历史恢复不会改写当轮身份；不同会话的选择独立。
- 图片附件经原消息结构进入 OpenAI Chat Completions 与 Responses 请求，保存后仍可重建；非视觉模型和失效专家保留输入并提示处理。
- HTML 仅在成功终态保存确认后自动预览，按运行去重；后台完成、未保存编辑、保存中、错误及历史加载均不会抢占页面。
- 内置项只读，不写入自建配置；可复制后定制。新增内置专家集中登记在 `core/conversation_agents.py`，选择器和管理页无需增加专用分支。

## 自动化结果

专家、工作区、统一管理、会话操作、产物链接五组共 **71 项通过**。最终补充隐藏上下文恢复场景后，专家组 **19 项再次通过**。命令从仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 -W ignore -c "import unittest; patterns=['test_conversation_agents.py','test_chat_workspace.py','test_unified_management.py','test_conversation_actions.py','test_deliverable_preview.py']; suite=unittest.TestSuite(unittest.defaultTestLoader.discover('test',pattern=p) for p in patterns); result=unittest.TextTestRunner(verbosity=1).run(suite); raise SystemExit(not result.wasSuccessful())"
```

另抽查 `test_conversation_linear_interactions.py` 中名称匹配 `history`、`submit`、`terminal`、`retry`、`assistant_turn_group`、`guidance` 的 50 项：43 项通过、6 项失败、1 项错误。以下同样在基线 `main.py` 上复现，未扩大到无关修复：

- `test_completed_run_does_not_reappend_tool_round_committed_before_guidance`
- `test_daemon_history_commit_error_keeps_visible_answer_with_reason`
- `test_diverged_pending_commit_keeps_current_history_and_persists_fallback`
- `test_empty_generated_merge_persists_visible_terminal_fallback`
- `test_render_failure_after_provider_success_keeps_completed_terminal`
- `test_terminal_save_barrier_keeps_actions_hidden_when_revision_fails`
- `test_guidance_boundary_routes_reentrant_thinking_to_new_group`（`StopIteration`）

## 界面验收

脚本隔离用户配置和工作目录，使用受控模型结果，实际操作主窗口、图片附件、QWebEngine 页面、键盘编辑、原子保存、重新打开与离线按钮交互；不是在线模型效果评测。

```powershell
.\.venv\Scripts\python.exe -X utf8 -W ignore scripts/smoke_conversation_agents.py --scale 1 --theme light
.\.venv\Scripts\python.exe -X utf8 -W ignore scripts/smoke_conversation_agents.py --scale 1.25 --theme light
.\.venv\Scripts\python.exe -X utf8 -W ignore scripts/smoke_conversation_agents.py --scale 1.5 --theme dark
```

三组均通过，包含专家名称和关闭按钮无重叠检查。离屏环境出现 GPU 上下文诊断，但页面加载、JavaScript 交互与编辑保存断言通过。

| 场景 | 浅色 100% | 浅色 125% | 深色预览 150% |
| --- | --- | --- | --- |
| 选择专家 | [截图](picker-light-100.png) | [截图](picker-light-125.png) | [截图](picker-dark-150.png) |
| 完成预览 | [截图](preview-light-100.png) | [截图](preview-light-125.png) | [截图](preview-dark-150.png) |
| 编辑内容 | [截图](edit-light-100.png) | [截图](edit-light-125.png) | [截图](edit-dark-150.png) |
| 管理专家 | [截图](management-light-100.png) | [截图](management-light-125.png) | [截图](management-dark-150.png) |

深色截图检查新增专家控件的主题接入，不代表原有文件编辑器所有控件的深色一致性已验收。小窗口编辑模式下原有聊天列仍可能被文件面板部分遮挡，本次确保专家选择和关闭可见；完整页面可在文件面板查看。

## 请求前缀

全局稳定系统提示词、工具 schema 与工具顺序未改。用户确认的专家角色通过既有尾部运行上下文追加，关闭时追加普通助手角色重置；不重写历史。用户消息保存提交时角色快照，assistant 的标题信息使用 `ui_` 展示字段，不参与规范账本签名。

尚未进行真实模型服务调用，因此图片复刻准确度、复杂页面生成质量仍需实际模型验收。
