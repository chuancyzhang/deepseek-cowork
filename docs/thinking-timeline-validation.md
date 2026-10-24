# 深度思考时间线验证

思考过程采用细时间线，点击“深度思考”直接展开或收起原文。原文顺序追加，工具调用和失败原因保持可见；工具“详情”继续通过现有信号打开右侧“任务详情 → 技术详情”。阶段回复、最终回答及整轮完成后的折叠规则沿用原实现。

此次改动位于 Qt 展示层，不修改消息存储、模型历史、流式事件顺序、运行控制、工具执行、提示词或缓存前缀。截图中的文字和工具结果均为隔离样例。

## 验证结果

- 13 项时间线相关回归通过：原位展开、原文折叠时工具可用、流式追加保留选择、停止后原文完整、复制原文、阶段顺序、最终结果与整体折叠、空阶段及动态高度。
- 28 项已有回归及 9 个子场景通过：运行事件持久化、隐藏正文延迟渲染、工具等待/完成/失败、主题预览取消、文件展示和长文本滚动。
- 实际 Qt 组件在 736 / 380 像素宽度、100% / 150% 缩放下检查；新增回归还覆盖 320 像素宽度。原文末行在视口内，复制、键盘操作可用。
- 真实 `MainWindow` 中点击工具“详情”及按 Enter 均打开右侧技术详情，核对工具 ID、所属会话、参数和返回结果。
- 深色主题使用隔离预览，取消后保存配置不变，恢复后的浅色截图逐像素一致。

原文高度验证发现两项显示问题：Qt 延迟计算未显示段落的换行；实际替换字体的行高可能超过编辑器的 `lineSpacing()`。高度缓存现使用实际段落边界，仍只重新测量变化的后缀，不重建原文。

另一次扩大筛选中，既有 `test_guidance_boundary_routes_reentrant_thinking_to_new_group` 在查找时间线文本时抛出 `StopIteration`；加载未修改的 `HEAD:main.py` 后同样复现。该项未修改，不计入通过数。三个旧显示用例原先未显示控件便读取延迟渲染内容，本次调整为显示并等待 Qt 更新后验证。未运行全仓测试或调用真实模型。

## 复现

在隔离 `APPDATA` / `LOCALAPPDATA`、`QT_QPA_PLATFORM=offscreen`、`COWORK_EXTERNAL_BACKGROUND_SERVICES=0` 的进程中执行：

```powershell
.venv/Scripts/python.exe -X utf8 -B -m pytest -q test/test_conversation_linear_interactions.py -k 'thinking_stays_expanded or completed_live_turn_folds or deep_thinking_fold or thinking_stream_fold or thinking_wrapped or empty_thinking_expansion or turn_group_keeps_multiple or first_turn_keeps_surplus or live_stage_growth or turn_group_hides_empty or stage_without_reply or stage_reply_remains or new_stage_freezes'
.venv/Scripts/python.exe -X utf8 -B -m pytest -q test/test_runtime_stream.py test/test_tool_tasks_ui.py test/test_conversation_runtime_fixes.py test/test_chat_scroll_regressions.py -k 'not new_gpt and not reasoning_effort'
```

截图脚本自行使用临时数据和主题仓库，不访问用户对话、不发送模型请求：

```powershell
.venv/Scripts/python.exe -X utf8 -B scripts/render_thinking_timeline.py
.venv/Scripts/python.exe -X utf8 -B scripts/render_thinking_timeline.py --scale 1.5
```

| 场景 | 100% | 150% |
| --- | --- | --- |
| 时间线 | [查看](images/thinking-timeline/timeline-light-1x.png) | [查看](images/thinking-timeline/timeline-light-1.5x.png) |
| 展开原文 | [查看](images/thinking-timeline/original-light-1x.png) | [查看](images/thinking-timeline/original-light-1.5x.png) |
| 窄窗口 | [查看](images/thinking-timeline/original-narrow-1x.png) | [查看](images/thinking-timeline/original-narrow-1.5x.png) |
| 流式追加 | [查看](images/thinking-timeline/streaming-narrow-1x.png) | [查看](images/thinking-timeline/streaming-narrow-1.5x.png) |
| 停止保留 | [查看](images/thinking-timeline/stopped-narrow-1x.png) | [查看](images/thinking-timeline/stopped-narrow-1.5x.png) |
| 深色主题 | [查看](images/thinking-timeline/original-dark-1x.png) | [查看](images/thinking-timeline/original-dark-1.5x.png) |
| 取消主题预览 | [查看](images/thinking-timeline/restored-light-1x.png) | [查看](images/thinking-timeline/restored-light-1.5x.png) |
| 右侧任务详情 | [查看](images/thinking-timeline/task-details-1x.png) | [查看](images/thinking-timeline/task-details-1.5x.png) |
