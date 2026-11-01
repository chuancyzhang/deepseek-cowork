# 统一任务工作区验证

验证日期：2026-11-01。

## 行为与边界

- 顶部统一为工作区入口。“任务”标签包含调用记录、子任务和执行上下文；具体工具、子任务与文件均从对话直接打开对应内容。
- 调用详情显示返回箭头、名称、状态及可信耗时，参数可折叠，运行信息按需展开。复制始终包含完整内容。
- 展开工作区后“对话”固定且不可关闭；其他标签统一关闭。关闭任务页保留运行、详情和子任务草稿，从全部标签可重新打开。
- 切换会话分别恢复调用选择、列表位置、结果选区和子任务草稿；延迟打开回调核对目标会话。查看文件、关闭后台文件标签或收到工具结果不改变当前任务视图。
- 任务页仅是会话内的界面状态，不进入 `WorkbenchStore`、模型工具列表或消息。系统提示词、工具定义及顺序、模型请求前缀均未改变。

## 自动检查

合计 60 项测试、21 个子测试通过：首次组合检查 58 项通过，随后单独验证原生网页 1 项、补充调用列表位置回归 1 项。

```powershell
New-Item -ItemType Directory -Force .tmp/unified-task-workspace | Out-Null
$env:TEMP=(Resolve-Path .tmp/unified-task-workspace).Path
$env:TMP=$env:TEMP
$env:QT_QPA_PLATFORM='offscreen'
.venv/Scripts/python.exe -X utf8 -B -m pytest -q test/test_task_workspace.py test/test_task_details.py test/test_browser_workbench.py test/test_subagent_panel.py test/test_writing_pages.py test/test_child_window_lifecycle.py -k 'not local_html_web_tool' -o cache_dir=.tmp/unified-task-workspace/pytest-cache
$env:QT_QPA_PLATFORM='windows'
.venv/Scripts/python.exe -X utf8 -B -m pytest -q test/test_browser_workbench.py -k local_html_web_tool -o cache_dir=.tmp/unified-task-workspace/native-pytest-cache
```

覆盖任务标签关闭与恢复、固定对话标签、跨会话详情与草稿、调用列表位置、结果选择与全量复制、后台结果刷新、文件关闭、页面保存与恢复、子任务停止与补充，以及控件生命周期。第一次包含真实 WebEngine 的离屏组合运行在本地 HTML 用例中触发 Chromium 合成线程崩溃，没有计为通过；该用例在 Windows 原生环境单独通过，其余用例在离屏环境通过。

## 原生界面与主题

```powershell
$env:QT_QPA_PLATFORM='windows'
.venv/Scripts/python.exe -X utf8 -B scripts/smoke_task_details_ui.py --output .tmp/unified-task-workspace/light
.venv/Scripts/python.exe -X utf8 -B scripts/smoke_task_details_ui.py --dark --compact --scale 1.5 --output .tmp/unified-task-workspace/dark
```

Windows 系统缩放 150%；浅色检查使用 `QT_SCALE_FACTOR=1`，实测 DPR 1.5；深色小窗口叠加 `QT_SCALE_FACTOR=1.5`，实测 DPR 2.25。截图前检查当前任务标签的关闭按钮中心在视口内。主题生效后、控件构造前监听 `QEvent.Show`，并覆盖显示后的导航、展开和切换：未发现非预期顶层窗口，Qt 回调错误为 0。深色预览取消后恢复原主题，已保存主题文件不变。

| 场景 | 截图 |
| --- | --- |
| 分栏工作区与调用直达 | [浅色](images/task-workspace/light/unified-workspace.png) |
| 调用详情 | [浅色](images/task-workspace/light/technical-details.png)、[深色小窗口](images/task-workspace/dark/technical-details.png) |
| 展开工作区 | [任务详情](images/task-workspace/light/workspace-expanded.png)、[固定对话](images/task-workspace/light/workspace-conversation.png) |
| 调用记录、子任务、上下文 | [调用记录](images/task-workspace/light/call-records.png)、[子任务](images/task-workspace/light/subtasks.png)、[执行上下文](images/task-workspace/light/execution-context.png) |
| 深色与缩放 | [整体](images/task-workspace/dark/unified-workspace.png)、[子任务](images/task-workspace/dark/subtasks.png)、[子任务对话与草稿](images/task-workspace/dark/subtask-conversation.png) |

截图使用隔离配置及合成数据；网页回归使用本地 HTML 和真实 Qt WebEngine。未调用真实模型或外部账号，未打包、提交或发布。
